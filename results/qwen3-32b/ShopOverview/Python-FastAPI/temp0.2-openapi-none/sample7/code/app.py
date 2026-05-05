from fastapi import FastAPI, Depends, HTTPException, status, Response
from fastapi.security import APIKeyCookie
from pydantic import BaseModel
import sqlite3
import os
import jwt
from datetime import datetime, timedelta
from passlib.context import CryptContext
import csv
from io import StringIO

app = FastAPI()

# Security setup
SECRET_KEY = os.getenv("APP_SECRET")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 1 day

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

cookie_scheme = APIKeyCookie(name="AUTH_COOKIE")

# Database setup
def get_db():
    db = sqlite3.connect("db.sqlite3")
    db.row_factory = sqlite3.Row
    return db

def create_tables():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS merchants (
                email TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                password TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS wares (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                price REAL NOT NULL,
                merchant_email TEXT NOT NULL,
                FOREIGN KEY (merchant_email) REFERENCES merchants(email)
            )
        """)
        db.commit()

create_tables()

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def get_current_merchant(cookie: str = Depends(cookie_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(cookie, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception
    return email

class MerchantRegister(BaseModel):
    email: str
    name: str
    password: str

class MerchantLogin(BaseModel):
    email: str
    password: str

@app.post("/merchants/register", status_code=201)
async def register_merchant(merchant: MerchantRegister):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM merchants WHERE email = ?", (merchant.email,))
    existing = cursor.fetchone()
    if existing:
        db.close()
        raise HTTPException(status_code=400, detail="Email already registered")
    hashed_password = pwd_context.hash(merchant.password)
    cursor.execute(
        "INSERT INTO merchants (email, name, password) VALUES (?, ?, ?)",
        (merchant.email, merchant.name, hashed_password)
    )
    db.commit()
    db.close()
    return {"message": "Merchant registered successfully"}

@app.post("/merchants/login")
async def login_merchant(merchant: MerchantLogin, response: Response):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM merchants WHERE email = ?", (merchant.email,))
    db_merchant = cursor.fetchone()
    if not db_merchant or not pwd_context.verify(merchant.password, db_merchant["password"]):
        db.close()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    access_token = create_access_token(data={"sub": merchant.email})
    response.set_cookie(key="AUTH_COOKIE", value=access_token, httponly=True)
    db.close()
    return {"message": "Login successful"}

@app.post("/merchants/upload-wares")
async def upload_wares(csv_data: dict, current_merchant: str = Depends(get_current_merchant)):
    csv_str = csv_data.get("csv", "")
    if not csv_str:
        raise HTTPException(status_code=400, detail="CSV data missing")
    wares = []
    try:
        f = StringIO(csv_str)
        reader = csv.reader(f)
        for row in reader:
            if len(row) != 3:
                raise ValueError("Invalid CSV format")
            name, description, price = row
            try:
                price = float(price)
            except ValueError:
                raise ValueError("Invalid price")
            wares.append((name, description, price, current_merchant))
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid CSV format")
    db = get_db()
    cursor = db.cursor()
    cursor.executemany(
        "INSERT INTO wares (name, description, price, merchant_email) VALUES (?, ?, ?, ?)",
        wares
    )
    db.commit()
    db.close()
    return {"message": "Wares uploaded successfully"}

@app.get("/wares")
async def get_wares(minPrice: float = None, maxPrice: float = None, current_merchant: str = Depends(get_current_merchant)):
    db = get_db()
    cursor = db.cursor()
    query = "SELECT * FROM wares"
    conditions = []
    params = []
    if minPrice is not None:
        conditions.append("price >= ?")
        params.append(minPrice)
    if maxPrice is not None:
        conditions.append("price <= ?")
        params.append(maxPrice)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    cursor.execute(query, params)
    wares = cursor.fetchall()
    db.close()
    html = "<table border='1'><tr><th>Name</th><th>Price</th><th>Description</th><th>Merchant Email</th></tr>"
    for ware in wares:
        html += f"<tr><td>{ware['name']}</td><td>{ware['price']}</td><td>{ware['description']}</td><td>{ware['merchant_email']}</td></tr>"
    html += "</table>"
    return Response(content=html, media_type="text/html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)