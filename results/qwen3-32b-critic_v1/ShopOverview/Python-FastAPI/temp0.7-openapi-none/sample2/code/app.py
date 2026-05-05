import os
import sqlite3
import jwt
import html
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime, timedelta
import csv
from io import StringIO
from starlette.responses import HTMLResponse
from passlib.context import CryptContext

app = FastAPI()
DATABASE = "db.sqlite3"
JWT_SECRET = os.environ.get("APP_SECRET")
if not JWT_SECRET:
    raise ValueError("APP_SECRET environment variable is not set")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with sqlite3.connect(DATABASE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS merchants (
                email TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                password TEXT NOT NULL
            )
        """)
        conn.execute("""
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

class RegisterRequest(BaseModel):
    email: EmailStr
    name: str
    password: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class UploadWaresRequest(BaseModel):
    csv: str

def create_jwt(email: str) -> str:
    payload = {
        "email": email,
        "exp": datetime.utcnow() + timedelta(hours=1)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def get_current_merchant(request: Request):
    cookie = request.cookies.get("AUTH_COOKIE")
    if not cookie:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        payload = jwt.decode(cookie, JWT_SECRET, algorithms=["HS256"])
        return payload["email"]
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Unauthorized")

@app.post("/merchants/register", status_code=201)
async def register_merchant(request: RegisterRequest):
    email = request.email
    name = request.name
    password = request.password
    hashed_password = pwd_context.hash(password)
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO merchants (email, name, password) VALUES (?, ?, ?)", 
                          (email, name, hashed_password))
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="Email already registered")
    return {"message": "Merchant registered successfully"}

@app.post("/merchants/login")
async def login_merchant(request: LoginRequest, response: Response):
    email = request.email
    password = request.password
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM merchants WHERE email = ?", (email,))
        merchant = cursor.fetchone()
    if not merchant or not pwd_context.verify(password, merchant['password']):
        raise HTTPException(
            status_code=401, 
            detail={"error": "Invalid email or password"}
        )
    token = create_jwt(email)
    response.set_cookie(key="AUTH_COOKIE", value=token)
    return "Login successful"

@app.post("/merchants/upload-wares")
async def upload_wares(
    request: UploadWaresRequest, 
    current_merchant: str = Depends(get_current_merchant)
):
    csv_data = request.csv
    merchant_email = current_merchant
    
    MAX_CSV_SIZE = 10000  # 10KB limit
    if len(csv_data) > MAX_CSV_SIZE:
        raise HTTPException(status_code=400, detail="CSV data too large")
        
    try:
        csv_file = StringIO(csv_data)
        csv_reader = csv.reader(csv_file)
        for row in csv_reader:
            if len(row) != 3:
                raise ValueError("Invalid row length")
            name, description, price_str = row
            try:
                price = float(price_str)
            except ValueError:
                raise ValueError("Invalid price")
            
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO wares (name, description, price, merchant_email) VALUES (?, ?, ?, ?)",
                    (name, description, price, merchant_email)
                )
                conn.commit()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid CSV format")
    
    return {"message": "Wares uploaded successfully"}

@app.get("/wares")
async def get_wares(
    minPrice: Optional[float] = None,
    maxPrice: Optional[float] = None
):
    query = """
        SELECT w.name, w.description, w.price, m.email as merchant_email 
        FROM wares w
        JOIN merchants m ON w.merchant_email = m.email
        WHERE 1=1
    """
    params = []
    
    if minPrice is not None:
        query += " AND w.price >= ?"
        params.append(minPrice)
    if maxPrice is not None:
        query += " AND w.price <= ?"
        params.append(maxPrice)
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        wares = cursor.fetchall()
    
    html = "<table border='1'><tr><th>Name</th><th>Description</th><th>Price</th><th>Merchant Email</th></tr>"
    for ware in wares:
        html += f"""
            <tr>
                <td>{html.escape(ware['name'])}</td>
                <td>{html.escape(ware['description'])}</td>
                <td>{html.escape(str(ware['price']))}</td>
                <td>{html.escape(ware['merchant_email'])}</td>
            </tr>
        """
    html += "</table>"
    
    return HTMLResponse(content=html)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)