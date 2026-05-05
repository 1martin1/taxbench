import os
import jwt
import csv
import io
from fastapi import FastAPI, Depends, HTTPException, Response, Cookie, Query
from fastapi.security import APIKeyCookie
from email_validator import validate_email, EmailNotValidError
from passlib.hash import bcrypt
import sqlite3
from typing import Annotated, Optional
from datetime import datetime, timedelta
from fastapi.responses import HTMLResponse

app = FastAPI()

DATABASE = 'db.sqlite3'
APP_SECRET = os.getenv("APP_SECRET")

def init_db():
    with sqlite3.connect(DATABASE) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS merchants
                        (email TEXT PRIMARY KEY, name TEXT NOT NULL, password_hash TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS wares
                        (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, description TEXT, price REAL NOT NULL, merchant_email TEXT NOT NULL,
                         FOREIGN KEY (merchant_email) REFERENCES merchants(email))''')

@app.on_event("startup")
def on_startup():
    init_db()

def get_current_merchant(
    auth_cookie: Annotated[str, Cookie()] = Cookie(...)
) -> str:
    try:
        payload = jwt.decode(auth_cookie, APP_SECRET, algorithms=["HS256"])
        return payload["sub"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Unauthorized")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Unauthorized")

@app.post("/merchants/register", status_code=201)
async def register_merchant(email: str, name: str, password: str):
    try:
        valid_email = validate_email(email).email
    except EmailNotValidError:
        raise HTTPException(status_code=400, detail="Invalid input")
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT email FROM merchants WHERE email = ?", (email,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Invalid input")
    password_hash = bcrypt.hash(password)
    with sqlite3.connect(DATABASE) as conn:
        conn.execute("INSERT INTO merchants (email, name, password_hash) VALUES (?, ?, ?)", (email, name, password_hash))
    return {"message": "Merchant registered successfully"}

@app.post("/merchants/login")
async def login_merchant(email: str, password: str, response: Response):
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT password_hash FROM merchants WHERE email = ?", (email,))
        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=401, detail={"error": "Invalid email or password"})
        stored_hash = result[0]
    if not bcrypt.verify(password, stored_hash):
        raise HTTPException(status_code=401, detail={"error": "Invalid email or password"})
    token = jwt.encode({"sub": email, "exp": datetime.utcnow() + timedelta(hours=1)}, APP_SECRET, algorithm="HS256")
    response.set_cookie(key="AUTH_COOKIE", value=token)
    return {"message": "Login successful"}

@app.post("/merchants/upload-wares")
async def upload_wares(
    csv: str,
    current_merchant: Annotated[str, Depends(get_current_merchant)]
):
    try:
        csv_file = io.StringIO(csv)
        reader = csv.DictReader(csv_file)
        if reader.fieldnames != ['name', 'description', 'price']:
            raise HTTPException(status_code=400, detail="Invalid CSV format")
        for row in reader:
            name = row['name']
            description = row['description']
            price_str = row['price']
            try:
                price = float(price_str)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid CSV format")
            with sqlite3.connect(DATABASE) as conn:
                conn.execute(
                    "INSERT INTO wares (name, description, price, merchant_email) VALUES (?, ?, ?, ?)",
                    (name, description, price, current_merchant)
                )
        return {"message": "Wares uploaded successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid CSV format")

@app.get("/wares")
async def get_wares(
    minPrice: Optional[float] = Query(None),
    maxPrice: Optional[float] = Query(None),
    current_merchant: Annotated[str, Depends(get_current_merchant)]
):
    query = """
        SELECT w.name, w.description, w.price, m.email as merchant_email
        FROM wares w
        JOIN merchants m ON w.merchant_email = m.email
        WHERE 1=1
    """
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
    html = "<table border='1'><tr><th>Name</th><th>Description</th><th>Price</th><th>Merchant Email</th></tr>"
    for row in rows:
        html += f"<tr><td>{row['name']}</td><td>{row['description']}</td><td>{row['price']}</td><td>{row['merchant_email']}</td></tr>"
    html += "</table>"
    return HTMLResponse(content=html, status_code=200)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)