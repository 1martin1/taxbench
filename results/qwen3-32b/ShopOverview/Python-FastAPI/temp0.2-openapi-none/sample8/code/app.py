import sqlite3
import os
import jwt
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from typing import Optional
from passlib.context import CryptContext
from datetime import datetime, timedelta
import csv
from io import StringIO

app = FastAPI()

APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise ValueError("APP_SECRET environment variable is required")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_db():
    db = sqlite3.connect('db.sqlite3')
    db.row_factory = sqlite3.Row
    return db

@app.on_event("startup")
def on_startup():
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS merchants (
            email TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            password_hash TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS wares (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            price REAL NOT NULL,
            merchant_email TEXT NOT NULL,
            FOREIGN KEY (merchant_email) REFERENCES merchants(email)
        )
    """)
    db.commit()

class RegisterRequest(BaseModel):
    email: str
    name: str
    password: str

@app.post("/merchants/register", status_code=201)
async def register_merchant(request: RegisterRequest):
    db = get_db()
    existing = db.execute("SELECT * FROM merchants WHERE email = ?", (request.email,)).fetchone()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    password_hash = pwd_context.hash(request.password)
    db.execute("INSERT INTO merchants (email, name, password_hash) VALUES (?, ?, ?)",
               (request.email, request.name, password_hash))
    db.commit()
    return {"message": "Merchant registered successfully"}

class LoginRequest(BaseModel):
    email: str
    password: str

@app.post("/merchants/login")
async def login_merchant(request: LoginRequest, response: Response):
    db = get_db()
    merchant = db.execute("SELECT * FROM merchants WHERE email = ?", (request.email,)).fetchone()
    if not merchant or not pwd_context.verify(request.password, merchant['password_hash']):
        raise HTTPException(status_code=401, detail={"error": "Invalid email or password"})
    payload = {
        "email": request.email,
        "exp": datetime.utcnow() + timedelta(hours=1)
    }
    token = jwt.encode(payload, APP_SECRET, algorithm="HS256")
    response.set_cookie(key="AUTH_COOKIE", value=token, httponly=True)
    return {"message": "Login successful"}

def get_current_merchant(request: Request):
    cookie = request.cookies.get("AUTH_COOKIE")
    if not cookie:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        payload = jwt.decode(cookie, APP_SECRET, algorithms=["HS256"])
        return payload["email"]
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Unauthorized")

class UploadWaresRequest(BaseModel):
    csv: str

@app.post("/merchants/upload-wares")
async def upload_wares(request: UploadWaresRequest, current_merchant: str = Depends(get_current_merchant)):
    csv_data = request.csv
    lines = csv_data.strip().split('\n')
    if not lines:
        raise HTTPException(status_code=400, detail="Invalid CSV format")
    headers = lines[0].split(',')
    if len(headers) != 3 or headers[0] != 'name' or headers[1] != 'description' or headers[2] != 'price':
        raise HTTPException(status_code=400, detail="Invalid CSV format")
    db = get_db()
    for line in lines[1:]:
        parts = line.split(',')
        if len(parts) != 3:
            raise HTTPException(status_code=400, detail="Invalid CSV format")
        name, description, price_str = parts
        try:
            price = float(price_str)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid price in CSV")
        db.execute("INSERT INTO wares (name, description, price, merchant_email) VALUES (?, ?, ?, ?)",
                   (name, description, price, current_merchant))
    db.commit()
    return {"message": "Wares uploaded successfully"}

@app.get("/wares")
async def get_wares(minPrice: Optional[float] = None, maxPrice: Optional[float] = None):
    db = get_db()
    query = "SELECT wares.name, wares.description, wares.price, merchants.email as merchant_email FROM wares JOIN merchants ON wares.merchant_email = merchants.email"
    conditions = []
    params = []
    if minPrice is not None:
        conditions.append("wares.price >= ?")
        params.append(minPrice)
    if maxPrice is not None:
        conditions.append("wares.price <= ?")
        params.append(maxPrice)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    wares = db.execute(query, params).fetchall()
    html = "<table border='1'><tr><th>Name</th><th>Description</th><th>Price</th><th>Merchant Email</th></tr>"
    for ware in wares:
        html += f"<tr><td>{ware['name']}</td><td>{ware['description']}</td><td>{ware['price']}</td><td>{ware['merchant_email']}</td></tr>"
    html += "</table>"
    return Response(content=html, media_type="text/html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)