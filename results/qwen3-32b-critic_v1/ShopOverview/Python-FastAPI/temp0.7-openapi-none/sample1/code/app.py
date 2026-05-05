from fastapi import Depends, FastAPI, HTTPException, Response, Query, APIKeyCookie
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr
import sqlite3
import os
import jwt
from datetime import datetime, timedelta
from passlib.context import CryptContext
import csv
import io
import html

app = FastAPI()

# Security setup
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
app_secret = os.getenv("APP_SECRET")
if not app_secret:
    raise ValueError("APP_SECRET environment variable is not set")

# Security scheme for cookie
cookie_scheme = APIKeyCookie(name="AUTH_COOKIE", auto_error=False)

# Database initialization
def init_db():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    # Create merchants table
    c.execute('''CREATE TABLE IF NOT EXISTS merchants
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  email TEXT UNIQUE NOT NULL,
                  name TEXT NOT NULL,
                  password TEXT NOT NULL)''')
    # Create wares table
    c.execute('''CREATE TABLE IF NOT EXISTS wares
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  merchant_email TEXT NOT NULL,
                  name TEXT NOT NULL,
                  description TEXT,
                  price REAL NOT NULL,
                  FOREIGN KEY(merchant_email) REFERENCES merchants(email))''')
    conn.commit()
    conn.close()

@app.on_event("startup")
def on_startup():
    init_db()

def get_db():
    conn = sqlite3.connect('db.sqlite3')
    return conn

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: timedelta):
    to_encode = data.copy()
    expire = datetime.utcnow() + expires_delta
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, app_secret, algorithm="HS256")
    return encoded_jwt

def get_current_merchant(cookie: str = Depends(cookie_scheme)):
    credentials_exception = HTTPException(
        status_code=401,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not cookie:
        raise credentials_exception
    try:
        payload = jwt.decode(cookie, app_secret, algorithms=["HS256"])
        email = payload.get("sub")
        if email is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception
    # Check if the merchant exists
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM merchants WHERE email = ?", (email,))
    merchant = c.fetchone()
    conn.close()
    if merchant is None:
        raise credentials_exception
    return email

# Pydantic models
class RegisterRequest(BaseModel):
    email: EmailStr
    name: str
    password: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class UploadWaresRequest(BaseModel):
    csv: str

@app.post("/merchants/register", status_code=201)
def register_merchant(request: RegisterRequest):
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO merchants (email, name, password) VALUES (?, ?, ?)",
                  (request.email, request.name, get_password_hash(request.password)))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Email already registered")
    finally:
        conn.close()
    return {"detail": "Merchant registered successfully"}

@app.post("/merchants/login")
def login_merchant(request: LoginRequest, response: Response):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM merchants WHERE email = ?", (request.email,))
    merchant = c.fetchone()
    conn.close()
    if not merchant or not verify_password(request.password, merchant[3]):
        raise HTTPException(status_code=401, detail={"error": "Invalid email or password"})
    # Create JWT token
    access_token_expires = timedelta(hours=1)
    access_token = create_access_token(
        data={"sub": request.email}, expires_delta=access_token_expires
    )
    # Set cookie with secure attributes
    response.set_cookie(
        key="AUTH_COOKIE", 
        value=access_token, 
        httponly=True,
        secure=True,
        samesite="Strict"
    )
    return {"message": "Login successful"}

@app.post("/merchants/upload-wares")
def upload_wares(request: UploadWaresRequest, current_merchant: str = Depends(get_current_merchant)):
    # Limit CSV size to 1MB
    if len(request.csv) > 1024 * 1024:
        raise HTTPException(status_code=413, detail="CSV file too large")
    
    conn = get_db()
    c = conn.cursor()
    
    try:
        csv_data = io.StringIO(request.csv.strip())
        csv_reader = csv.reader(csv_data)
        
        # Validate header (if present)
        try:
            header = next(csv_reader)
            if header != ['name', 'description', 'price']:
                raise ValueError("Invalid CSV header")
        except StopIteration:
            raise HTTPException(status_code=400, detail="Empty CSV")
        
        for line_number, row in enumerate(csv_reader, 1):
            if len(row) != 3:
                raise HTTPException(status_code=400, detail=f"Invalid CSV format at line {line_number}")
            
            name, description, price_str = row
            
            try:
                price = float(price_str)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid price value at line {line_number}")
            
            c.execute(
                "INSERT INTO wares (merchant_email, name, description, price) VALUES (?, ?, ?, ?)",
                (current_merchant, name, description, price)
            )
        
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()
    
    return {"detail": "Wares uploaded successfully"}

@app.get("/wares")
def get_wares(minPrice: float = Query(None), maxPrice: float = Query(None)):
    conn = get_db()
    c = conn.cursor()
    query = """
        SELECT w.name, w.description, w.price, m.email 
        FROM wares w
        JOIN merchants m ON w.merchant_email = m.email
    """
    conditions = []
    params = []
    if minPrice is not None:
        conditions.append("w.price >= ?")
        params.append(minPrice)
    if maxPrice is not None:
        conditions.append("w.price <= ?")
        params.append(maxPrice)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    
    # Generate HTML table with escaped content
    html = "<table border='1'><tr><th>Name</th><th>Description</th><th>Price</th><th>Merchant Email</th></tr>"
    for row in rows:
        html += f"<tr><td>{html.escape(str(row[0]))}</td><td>{html.escape(str(row[1]))}</td><td>{html.escape(str(row[2]))}</td><td>{html.escape(str(row[3]))}</td></tr>"
    html += "</table>"
    return HTMLResponse(content=html, status_code=200)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)