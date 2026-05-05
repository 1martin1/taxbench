import os
import sqlite3
import csv
import io
from fastapi import FastAPI, Depends, HTTPException, Response, Cookie
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime, timedelta
import jwt
from email_validator import validate_email, EmailNotValidError
from passlib.context import CryptContext

app = FastAPI()

DATABASE = 'db.sqlite3'
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Initialize database
def init_db():
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS merchants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS wares (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                merchant_email TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                price REAL NOT NULL,
                FOREIGN KEY (merchant_email) REFERENCES merchants(email)
            )
        ''')
        conn.commit()

init_db()

# Pydantic models
class RegisterRequest(BaseModel):
    email: EmailStr
    name: str
    password: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class WareUploadRequest(BaseModel):
    csv: str

class WareResponse(BaseModel):
    id: int
    merchant_email: str
    name: str
    description: str
    price: float

# JWT functions
def create_access_token(data: dict, expires_delta: timedelta):
    to_encode = data.copy()
    expire = datetime.utcnow() + expires_delta
    to_encode.update({"exp": expire})
    SECRET_KEY = os.getenv("APP_SECRET")
    if not SECRET_KEY:
        raise ValueError("APP_SECRET environment variable not set")
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm="HS256")
    return encoded_jwt

def verify_token(token: str):
    try:
        SECRET_KEY = os.getenv("APP_SECRET")
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# Dependency for authentication
async def get_current_user(token: str = Cookie(None)):
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return verify_token(token)

# Endpoints
@app.post("/merchants/register", status_code=201)
async def register_merchant(request: RegisterRequest):
    try:
        validate_email(request.email)
    except EmailNotValidError:
        raise HTTPException(status_code=400, detail="Invalid email")
    
    hashed_password = pwd_context.hash(request.password)
    
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO merchants (email, name, password_hash) VALUES (?, ?, ?)",
                (request.email, request.name, hashed_password)
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="Email already registered")
    return {"message": "Merchant registered successfully"}

@app.post("/merchants/login")
async def login_merchant(request: LoginRequest):
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT password_hash FROM merchants WHERE email = ?", (request.email,))
        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=401, detail="Invalid email or password")
        stored_hash = result[0]
        if not pwd_context.verify(request.password, stored_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        
        access_token_expires = timedelta(hours=1)
        token = create_access_token(
            data={"sub": request.email}, expires_delta=access_token_expires
        )
        response = Response(content='{"message": "Login successful"}', media_type="application/json")
        response.set_cookie(key="AUTH_COOKIE", value=token)
        return response

@app.post("/merchants/upload-wares")
async def upload_wares(request: WareUploadRequest, user: dict = Depends(get_current_user)):
    merchant_email = user["sub"]
    csv_data = request.csv
    
    wares = []
    csv_io = io.StringIO(csv_data)
    reader = csv.reader(csv_io)
    for row in reader:
        if len(row) < 3:
            raise HTTPException(status_code=400, detail="Invalid CSV format")
        name, description, price_str = row[0], row[1], row[2]
        try:
            price = float(price_str)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid price format")
        wares.append((merchant_email, name, description, price))
    
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.executemany(
            "INSERT INTO wares (merchant_email, name, description, price) VALUES (?, ?, ?, ?)",
            wares
        )
        conn.commit()
    return {"message": "Wares uploaded successfully"}

@app.get("/wares")
async def get_wares(
    minPrice: Optional[float] = None,
    maxPrice: Optional[float] = None,
    user: dict = Depends(get_current_user)
):
    query = "SELECT * FROM wares WHERE 1=1"
    params = []
    if minPrice is not None:
        query += " AND price >= ?"
        params.append(minPrice)
    if maxPrice is not None:
        query += " AND price <= ?"
        params.append(maxPrice)
    
    with sqlite3.connect(DATABASE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
    
    html = "<html><body><table border='1'>"
    html += "<tr><th>Name</th><th>Price</th><th>Description</th><th>Merchant Email</th></tr>"
    for row in rows:
        html += f"<tr>"
        html += f"<td>{row['name']}</td>"
        html += f"<td>{row['price']}</td>"
        html += f"<td>{row['description']}</td>"
        html += f"<td>{row['merchant_email']}</td>"
        html += "</tr>"
    html += "</table></body></html>"
    
    return Response(content=html, media_type="text/html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)