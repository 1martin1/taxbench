import os
import sqlite3
import jwt
from fastapi import FastAPI, Depends, HTTPException, status, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from passlib.context import CryptContext
from datetime import datetime, timedelta
from email_validator import validate_email, EmailNotValidError
from typing import Optional

app = FastAPI()

# Load environment variables
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise ValueError("APP_SECRET environment variable is not set")

# Database setup
def init_db():
    with sqlite3.connect("db.sqlite3") as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS merchants (
                email TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS wares (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                price REAL NOT NULL,
                merchant_email TEXT NOT NULL,
                FOREIGN KEY (merchant_email) REFERENCES merchants(email)
            )
        """)
        conn.commit()

init_db()

# Password context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT settings
JWT_SECRET = APP_SECRET
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Dependency to get database connection
def get_db():
    conn = sqlite3.connect("db.sqlite3")
    try:
        yield conn
    finally:
        conn.close()

# Authentication functions
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET, algorithm=ALGORITHM)
    return encoded_jwt

def get_current_merchant(request: Request):
    auth_cookie = request.cookies.get("AUTH_COOKIE")
    if not auth_cookie:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = jwt.decode(auth_cookie, JWT_SECRET, algorithms=[ALGORITHM])
        merchant_email = payload.get("sub")
        if merchant_email is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
            )
        return merchant_email
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )

@app.post("/merchants/register", status_code=201)
async def register_merchant(email: str, name: str, password: str):
    # Validate email
    try:
        validate_email(email).email
    except EmailNotValidError:
        raise HTTPException(status_code=400, detail="Invalid email format")
    
    # Check if merchant exists
    with sqlite3.connect("db.sqlite3") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM merchants WHERE email = ?", (email,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Email already registered")
        
        # Hash password
        hashed_password = get_password_hash(password)
        cursor.execute(
            "INSERT INTO merchants (email, name, password_hash) VALUES (?, ?, ?)",
            (email, name, hashed_password)
        )
        conn.commit()
    return {"message": "Merchant registered successfully"}

@app.post("/merchants/login")
async def login_merchant(email: str, password: str):
    with sqlite3.connect("db.sqlite3") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT password_hash FROM merchants WHERE email = ?", (email,))
        result = cursor.fetchone()
        if not result:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password"
            )
        stored_hash = result[0]
        if not verify_password(password, stored_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password"
            )
    
    # Create token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": email}, expires_delta=access_token_expires
    )
    
    response = JSONResponse(content={"message": "Login successful"})
    response.set_cookie(key="AUTH_COOKIE", value=access_token, httponly=True)
    return response

@app.post("/merchants/upload-wares")
async def upload_wares(request: Request, csv: str, db: sqlite3.Connection = Depends(get_db)):
    merchant_email = get_current_merchant(request)
    # Parse CSV
    lines = csv.strip().split('\n')
    if not lines:
        raise HTTPException(status_code=400, detail="Empty CSV")
    # Check headers
    headers = lines[0].split(',')
    if len(headers) != 3 or headers[0].lower() != 'name' or headers[1].lower() != 'description' or headers[2].lower() != 'price':
        raise HTTPException(status_code=400, detail="Invalid CSV format")
    
    for line in lines[1:]:
        parts = line.split(',')
        if len(parts) != 3:
            raise HTTPException(status_code=400, detail="Invalid CSV format")
        name, description, price_str = parts
        try:
            price = float(price_str)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid price format")
        
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO wares (name, description, price, merchant_email) VALUES (?, ?, ?, ?)",
            (name, description, price, merchant_email)
        )
    db.commit()
    return {"message": "Wares uploaded successfully"}

@app.get("/wares", response_class=HTMLResponse)
async def get_wares(request: Request, minPrice: Optional[float] = None, maxPrice: Optional[float] = None, db: sqlite3.Connection = Depends(get_db)):
    merchant_email = get_current_merchant(request)  # This is required by the schema
    
    # Build query
    base_query = "SELECT name, description, price, merchant_email FROM wares"
    conditions = []
    params = []
    if minPrice is not None:
        conditions.append("price >= ?")
        params.append(minPrice)
    if maxPrice is not None:
        conditions.append("price <= ?")
        params.append(maxPrice)
    if conditions:
        base_query += " WHERE " + " AND ".join(conditions)
    
    cursor = db.cursor()
    cursor.execute(base_query, params)
    wares = cursor.fetchall()
    
    # Generate HTML table
    html = """
    <table border="1">
      <tr>
        <th>Ware Name</th>
        <th>Price</th>
        <th>Description</th>
        <th>Merchant Email</th>
      </tr>
    """
    for name, description, price, merchant_email in wares:
        html += f"""
      <tr>
        <td>{name}</td>
        <td>{price}</td>
        <td>{description}</td>
        <td>{merchant_email}</td>
      </tr>
        """
    html += "</table>"
    
    return html

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)