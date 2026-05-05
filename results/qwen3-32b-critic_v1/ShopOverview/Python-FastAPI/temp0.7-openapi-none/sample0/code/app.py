import os
import sqlite3
import jwt
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Depends, Request, Query, Response
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel
from email_validator import EmailStr
from typing import Optional
from passlib.context import CryptContext
import csv
from io import StringIO
import html
import uvicorn

# Configuration
SECRET = os.getenv("APP_SECRET")
if not SECRET:
    raise ValueError("APP_SECRET environment variable not set")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# FastAPI app
app = FastAPI()

# Database connection
def get_db():
    return sqlite3.connect('db.sqlite3')

# Create tables on startup
@app.on_event("startup")
async def startup():
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS merchants (
                        email TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        password_hash TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS wares (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        description TEXT,
                        price REAL NOT NULL,
                        merchant_email TEXT NOT NULL,
                        FOREIGN KEY (merchant_email) REFERENCES merchants(email))''')
    conn.commit()
    conn.close()

# JWT utilities
def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET, algorithm=ALGORITHM)

# Password utilities
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

# Dependency to get current merchant
async def get_current_merchant(request: Request):
    cookie = request.cookies.get("AUTH_COOKIE")
    if not cookie:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        payload = jwt.decode(cookie, SECRET, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return email
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# Pydantic models
class MerchantRegister(BaseModel):
    email: EmailStr
    name: str
    password: str

class MerchantLogin(BaseModel):
    email: EmailStr
    password: str

# Endpoints
@app.post("/merchants/register")
async def register_merchant(merchant: MerchantRegister):
    conn = get_db()
    cursor = conn.cursor()
    try:
        # Check if email exists
        cursor.execute("SELECT * FROM merchants WHERE email = ?", (merchant.email,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Email already registered")
        
        # Hash password and insert
        hashed_password = get_password_hash(merchant.password)
        cursor.execute("INSERT INTO merchants (email, name, password_hash) VALUES (?, ?, ?)",
                      (merchant.email, merchant.name, hashed_password))
        conn.commit()
        return Response(status_code=201)
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()

@app.post("/merchants/login")
async def login_merchant(merchant: MerchantLogin):
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        # Get merchant
        cursor.execute("SELECT email, password_hash FROM merchants WHERE email = ?", (merchant.email,))
        db_merchant = cursor.fetchone()
        
        if not db_merchant or not verify_password(merchant.password, db_merchant[1]):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        
        # Create token
        token = create_access_token(data={"sub": db_merchant[0]})
        response = JSONResponse(content={"message": "Login successful"})
        response.headers["Set-Cookie"] = f"AUTH_COOKIE={token}; Path=/"
        return response
    except Exception as e:
        raise
    finally:
        if conn:
            conn.close()

@app.post("/merchants/upload-wares")
async def upload_wares(
    request: Request,
    current_merchant: str = Depends(get_current_merchant)
):
    content = await request.json()
    csv_data = content.get("csv", "")
    
    # Input validation
    if len(csv_data.encode('utf-8')) > 1 * 1024 * 1024:  # 1MB limit
        raise HTTPException(status_code=413, detail="CSV payload too large")
    
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        # Parse CSV
        f = StringIO(csv_data)
        reader = csv.reader(f)
        rows = list(reader)
        
        # Validate rows
        if not rows or len(rows[0]) != 3:
            raise HTTPException(status_code=400, detail="Invalid CSV format")
            
        if len(rows) > 1000:  # Limit to 1000 rows
            raise HTTPException(status_code=400, detail="Too many rows in CSV")
            
        # Insert wares
        for row in rows:
            if len(row) != 3:
                raise HTTPException(status_code=400, detail="Invalid CSV format")
            name, description, price = row
            try:
                price_float = float(price)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid price value")
                
            cursor.execute(
                "INSERT INTO wares (name, description, price, merchant_email) VALUES (?, ?, ?, ?)",
                (name, description, price_float, current_merchant)
            )
            
        conn.commit()
        return {"message": "Wares uploaded successfully"}
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()

@app.get("/wares")
async def get_wares(
    current_merchant: str = Depends(get_current_merchant),
    minPrice: Optional[float] = Query(None),
    maxPrice: Optional[float] = Query(None)
):
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        # Build query
        query = "SELECT name, description, price, merchant_email FROM wares WHERE 1=1"
        params = []
        
        if minPrice is not None:
            query += " AND price >= ?"
            params.append(minPrice)
            
        if maxPrice is not None:
            query += " AND price <= ?"
            params.append(maxPrice)
            
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        # Generate HTML table with escaped content
        html = "<table border='1'><tr>"
        html += "<th>Name</th><th>Description</th><th>Price</th><th>Merchant Email</th>"
        html += "</tr>"
        
        for row in rows:
            html += f"<tr><td>{html.escape(row[0])}</td><td>{html.escape(row[1])}</td><td>{html.escape(str(row[2]))}</td><td>{html.escape(row[3])}</td></tr>"
            
        html += "</table>"
        
        return HTMLResponse(content=html)
    except Exception as e:
        raise
    finally:
        conn.close()

# Run the server
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)