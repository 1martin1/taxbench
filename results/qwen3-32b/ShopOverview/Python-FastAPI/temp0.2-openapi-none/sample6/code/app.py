import os
import csv
import sqlite3
import jwt
from fastapi import FastAPI, Depends, HTTPException, Response, Query
from fastapi.security import APIKeyCookie
from pydantic import BaseModel
from typing import List, Optional
from dotenv import load_dotenv
from starlette.templating import Jinja2Templates
from starlette.requests import Request
from starlette.responses import HTMLResponse
from email_validator import validate_email, EmailNotValidError
from passlib.context import CryptContext
from datetime import datetime, timedelta

load_dotenv()

APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise ValueError("APP_SECRET environment variable is required")

JWT_EXPIRATION = 3600  # 1 hour

app = FastAPI()
templates = Jinja2Templates(directory=".")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
cookie_scheme = APIKeyCookie(name="AUTH_COOKIE", auto_error=False)

# Database initialization
def init_db():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS merchants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
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
            merchant_id INTEGER NOT NULL,
            FOREIGN KEY (merchant_id) REFERENCES merchants(id)
        )
    """)
    conn.commit()
    conn.close()

init_db()

# Security dependencies
def verify_token(token: str = Depends(cookie_scheme)):
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=["HS256"])
        return payload["sub"]
    except:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

# Request models
class RegisterRequest(BaseModel):
    email: str
    name: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class UploadWaresRequest(BaseModel):
    csv: str

class WareResponse(BaseModel):
    name: str
    description: str
    price: float
    merchant_email: str

# Helper functions
def get_db_connection():
    conn = sqlite3.connect("db.sqlite3")
    conn.row_factory = sqlite3.Row
    return conn

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

# Endpoints
@app.post("/merchants/register", status_code=201)
def register_merchant(request: RegisterRequest):
    try:
        validate_email(request.email)
    except EmailNotValidError:
        raise HTTPException(status_code=400, detail="Invalid email format")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            "INSERT INTO merchants (email, name, password_hash) VALUES (?, ?, ?)",
            (request.email, request.name, hash_password(request.password))
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Email already registered")
    finally:
        conn.close()
    
    return {"message": "Merchant registered successfully"}

@app.post("/merchants/login")
def login_merchant(request: LoginRequest, response: Response):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, password_hash FROM merchants WHERE email = ?", (request.email,))
    merchant = cursor.fetchone()
    conn.close()
    
    if not merchant or not verify_password(request.password, merchant["password_hash"]):
        raise HTTPException(
            status_code=401,
            detail={"error": "Invalid email or password"}
        )
    
    token = jwt.encode(
        {
            "sub": request.email,
            "exp": datetime.utcnow() + timedelta(seconds=JWT_EXPIRATION)
        },
        APP_SECRET,
        algorithm="HS256"
    )
    
    response.set_cookie(key="AUTH_COOKIE", value=token, httponly=True)
    return {"message": "Login successful"}

@app.post("/merchants/upload-wares")
def upload_wares(
    request: UploadWaresRequest,
    current_email: str = Depends(verify_token)
):
    lines = request.csv.strip().splitlines()
    if not lines:
        raise HTTPException(status_code=400, detail="Empty CSV")
    
    header = lines[0].lower()
    if "name,description,price" not in header:
        raise HTTPException(status_code=400, detail="Invalid CSV format")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id FROM merchants WHERE email = ?", (current_email,))
    merchant = cursor.fetchone()
    if not merchant:
        raise HTTPException(status_code=401, detail="Unauthorized")
    merchant_id = merchant["id"]
    
    errors = []
    for line in lines[1:]:
        try:
            name, description, price = line.split(",", 2)
            price = float(price)
            
            cursor.execute(
                "INSERT INTO wares (name, description, price, merchant_id) VALUES (?, ?, ?, ?)",
                (name, description, price, merchant_id)
            )
        except ValueError:
            errors.append(f"Invalid format in line: {line}")
    
    conn.commit()
    conn.close()
    
    if errors:
        raise HTTPException(status_code=400, detail="\n".join(errors))
    
    return {"message": "Wares uploaded successfully"}

@app.get("/wares", response_class=HTMLResponse)
def get_wares(
    minPrice: Optional[float] = Query(None),
    maxPrice: Optional[float] = Query(None),
    current_email: str = Depends(verify_token)
):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = """
        SELECT w.name, w.description, w.price, m.email as merchant_email
        FROM wares w
        JOIN merchants m ON w.merchant_id = m.id
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
    
    cursor.execute(query, params)
    wares = cursor.fetchall()
    conn.close()
    
    return templates.TemplateResponse("wares.html", {
        "request": Request,
        "wares": wares
    })

# HTML template
@app.get("/static/wares.html")
def get_wares_template():
    return HTMLResponse("""
        <table border="1" style="width:100%; border-collapse: collapse;">
            <thead>
                <tr>
                    <th style="padding: 8px; text-align: left;">Name</th>
                    <th style="padding: 8px; text-align: left;">Description</th>
                    <th style="padding: 8px; text-align: left;">Price</th>
                    <th style="padding: 8px; text-align: left;">Merchant Email</th>
                </tr>
            </thead>
            <tbody>
                {% for ware in wares %}
                <tr>
                    <td style="padding: 6px; border: 1px solid #ccc;">{{ ware.name }}</td>
                    <td style="padding: 6px; border: 1px solid #ccc;">{{ ware.description }}</td>
                    <td style="padding: 6px; border: 1px solid #ccc;">{{ '%.2f' | format(ware.price) }}</td>
                    <td style="padding: 6px; border: 1px solid #ccc;">{{ ware.merchant_email }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    """)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)