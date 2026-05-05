from fastapi import FastAPI, Depends, HTTPException, Response, Query, Request
from fastapi.security import APIKeyCookie
from pydantic import BaseModel, EmailStr
import sqlite3
import os
import csv
import io
import jwt
from datetime import datetime, timedelta
from passlib.context import CryptContext
from jinja2 import Template

app = FastAPI()

# Configuration
SECRET_KEY = os.getenv("APP_SECRET")
if not SECRET_KEY:
    raise ValueError("APP_SECRET environment variable not set")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Security scheme
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
                password_hash TEXT NOT NULL
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

# Models
class MerchantRegister(BaseModel):
    email: EmailStr
    name: str
    password: str

class MerchantLogin(BaseModel):
    email: EmailStr
    password: str

class WareCSV(BaseModel):
    csv: str

class WareResponse(BaseModel):
    name: str
    description: str
    price: float
    merchant_email: str

# Authentication
def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_merchant(request: Request):
    token = request.cookies.get("AUTH_COOKIE")
    if not token:
        raise HTTPException(status_code=401, detail={"error": "Unauthorized"})
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        merchant_email = payload.get("sub")
        if not merchant_email:
            raise HTTPException(status_code=401, detail={"error": "Invalid token"})
        return merchant_email
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail={"error": "Invalid token"})

# Endpoints
@app.post("/merchants/register", status_code=201)
async def register_merchant(merchant: MerchantRegister):
    db = get_db()
    try:
        existing = db.execute("SELECT * FROM merchants WHERE email = ?", (merchant.email,)).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="Email already registered")
        
        password_hash = pwd_context.hash(merchant.password)
        db.execute("INSERT INTO merchants (email, name, password_hash) VALUES (?, ?, ?)",
                   (merchant.email, merchant.name, password_hash))
        db.commit()
    finally:
        db.close()
    return {"message": "Merchant registered successfully"}

@app.post("/merchants/login")
async def login_merchant(merchant: MerchantLogin, response: Response):
    db = get_db()
    try:
        result = db.execute("SELECT * FROM merchants WHERE email = ?", (merchant.email,)).fetchone()
        if not result or not pwd_context.verify(merchant.password, result['password_hash']):
            raise HTTPException(status_code=401, detail={"error": "Invalid email or password"})
        
        token = create_access_token({"sub": merchant.email})
        response.set_cookie(key="AUTH_COOKIE", value=token, httponly=True)
        return {"message": "Login successful"}
    finally:
        db.close()

@app.post("/merchants/upload-wares")
async def upload_wares(wares: WareCSV, current_merchant: str = Depends(get_current_merchant)):
    csv_data = wares.csv
    db = get_db()
    try:
        reader = csv.DictReader(io.StringIO(csv_data))
        for row in reader:
            if 'name' not in row or 'description' not in row or 'price' not in row:
                raise HTTPException(status_code=400, detail="Invalid CSV format")
            try:
                price = float(row['price'])
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid CSV format")
            
            db.execute("""
                INSERT INTO wares (name, description, price, merchant_email)
                VALUES (?, ?, ?, ?)
            """, (row['name'], row['description'], price, current_merchant))
        db.commit()
    finally:
        db.close()
    return {"message": "Wares uploaded successfully"}

@app.get("/wares")
async def get_wares(minPrice: Optional[float] = Query(None), maxPrice: Optional[float] = Query(None)):
    db = get_db()
    try:
        query = "SELECT * FROM wares WHERE 1=1"
        params = []
        if minPrice is not None:
            query += " AND price >= ?"
            params.append(minPrice)
        if maxPrice is not None:
            query += " AND price <= ?"
            params.append(maxPrice)
        
        wares = db.execute(query, params).fetchall()
        
        html_template = """
        <html>
        <head><title>Wares</title></head>
        <body>
            <h1>Available Wares</h1>
            <table border="1">
                <tr>
                    <th>Name</th>
                    <th>Description</th>
                    <th>Price</th>
                    <th>Merchant Email</th>
                </tr>
                {% for ware in wares %}
                <tr>
                    <td>{{ ware.name }}</td>
                    <td>{{ ware.description }}</td>
                    <td>{{ ware.price }}</td>
                    <td>{{ ware.merchant_email }}</td>
                </tr>
                {% endfor %}
            </table>
        </body>
        </html>
        """
        template = Template(html_template)
        return Response(content=template.render(wares=wares), media_type="text/html")
    finally:
        db.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)