import os
import sqlite3
import jwt
import csv
from io import StringIO
from fastapi import FastAPI, Depends, HTTPException, status, Request, Response
from fastapi.security import APIKeyCookie
from typing import Optional, List, Dict
import datetime
import html

app = FastAPI()

# Database setup
DATABASE_NAME = "db.sqlite3"

def get_db():
    db = sqlite3.connect(DATABASE_NAME)
    db.row_factory = sqlite3.Row  # To access columns by name
    return db

@app.on_event("startup")
def startup():
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS merchants (
            email TEXT PRIMARY KEY,
            name TEXT,
            password TEXT
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS wares (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            description TEXT,
            price REAL,
            merchant_email TEXT,
            FOREIGN KEY(merchant_email) REFERENCES merchants(email)
        )
    """)
    db.commit()
    db.close()

# Configuration
SECRET_KEY = os.getenv("APP_SECRET")
if not SECRET_KEY:
    raise ValueError("APP_SECRET environment variable not set")
AUTH_COOKIE = "AUTH_COOKIE"

def get_current_merchant(request: Request):
    cookie = request.cookies.get(AUTH_COOKIE)
    if not cookie:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    try:
        payload = jwt.decode(cookie, SECRET_KEY, algorithms=["HS256"])
        email = payload.get("sub")
        if not email:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
        db = get_db()
        merchant = db.execute("SELECT * FROM merchants WHERE email = ?", (email,)).fetchone()
        db.close()
        if not merchant:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
        return email
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

@app.post("/merchants/register", status_code=status.HTTP_201_CREATED)
async def register_merchant(request: Request):
    data = await request.json()
    email = data.get("email")
    name = data.get("name")
    password = data.get("password")
    
    if not all([email, name, password]):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing required fields")
    
    db = get_db()
    existing_merchant = db.execute("SELECT * FROM merchants WHERE email = ?", (email,)).fetchone()
    if existing_merchant:
        db.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
    
    db.execute("INSERT INTO merchants (email, name, password) VALUES (?, ?, ?)", (email, name, password))
    db.commit()
    db.close()
    return {"message": "Merchant registered successfully"}

@app.post("/merchants/login")
async def login_merchant(request: Request, response: Response):
    data = await request.json()
    email = data.get("email")
    password = data.get("password")
    
    if not email or not password:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    
    db = get_db()
    merchant = db.execute("SELECT * FROM merchants WHERE email = ? AND password = ?", (email, password)).fetchone()
    db.close()
    if not merchant:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    
    payload = {
        "sub": email,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1)
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
    
    response.set_cookie(key=AUTH_COOKIE, value=token, httponly=True)
    return {"message": "Login successful"}

@app.post("/merchants/upload-wares")
async def upload_wares(request: Request, current_merchant: str = Depends(get_current_merchant)):
    data = await request.json()
    csv_str = data.get("csv")
    
    if not csv_str:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CSV is required")
    
    try:
        f = StringIO(csv_str)
        csv_reader = csv.reader(f)
        
        db = get_db()
        for row in csv_reader:
            if len(row) != 3:
                db.close()
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid CSV format")
            
            name, description, price_str = row
            
            try:
                price = float(price_str)
            except ValueError:
                db.close()
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid price format")
            
            db.execute("INSERT INTO wares (name, description, price, merchant_email) VALUES (?, ?, ?, ?)", 
                       (name, description, price, current_merchant))
        db.commit()
        db.close()
            
    except Exception as e:
        db.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid CSV format")
    
    return {"message": "Wares uploaded successfully"}

@app.get("/wares")
async def get_wares(request: Request, 
                    current_merchant: str = Depends(get_current_merchant),
                    minPrice: Optional[float] = None,
                    maxPrice: Optional[float] = None):
    db = get_db()
    query = "SELECT * FROM wares"
    params = []
    
    if minPrice is not None or maxPrice is not None:
        conditions = []
        if minPrice is not None:
            conditions.append("price >= ?")
            params.append(minPrice)
        if maxPrice is not None:
            conditions.append("price <= ?")
            params.append(maxPrice)
        query += " WHERE " + " AND ".join(conditions)
    
    cursor = db.execute(query, params)
    wares = cursor.fetchall()
    db.close()
    
    html = "<html><body><table border='1'>"
    html += "<tr><th>Name</th><th>Description</th><th>Price</th><th>Merchant Email</th></tr>"
    
    for ware in wares:
        name = html.escape(ware['name'])
        description = html.escape(ware['description'])
        price = ware['price']
        merchant_email = html.escape(ware['merchant_email'])
        html += f"<tr><td>{name}</td><td>{description}</td><td>{price}</td><td>{merchant_email}</td></tr>"
    
    html += "</table></body></html>"
    
    return Response(content=html, media_type="text/html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)