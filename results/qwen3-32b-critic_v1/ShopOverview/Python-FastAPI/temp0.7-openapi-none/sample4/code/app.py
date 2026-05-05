import os
import sqlite3
import csv
import io
from datetime import datetime, timedelta
from fastapi import FastAPI, Depends, HTTPException, Response, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import APIKeyCookie
from pydantic import BaseModel
from typing import Optional, List
import jwt
from passlib.context import CryptContext

app = FastAPI()

APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise ValueError("APP_SECRET environment variable is not set")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def create_tables():
    try:
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS merchants (
                email TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                hashed_password TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS wares (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                price REAL NOT NULL,
                merchant_email TEXT NOT NULL,
                FOREIGN KEY (merchant_email) REFERENCES merchants(email)
            )
        ''')
        conn.commit()
    except Exception as e:
        raise RuntimeError(f"Failed to create tables: {str(e)}")
    finally:
        try:
            conn.close()
        except:
            pass

@app.on_event("startup")
def on_startup():
    create_tables()

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, APP_SECRET, algorithm=ALGORITHM)

def get_current_merchant(request: Request):
    auth_cookie = request.cookies.get("AUTH_COOKIE")
    if not auth_cookie:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        payload = jwt.decode(auth_cookie, APP_SECRET, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return email
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

class RegisterRequest(BaseModel):
    email: str
    name: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class UploadWaresRequest(BaseModel):
    csv: str

@app.post("/merchants/register", status_code=201, timeout=30)
async def register_merchant(register_data: RegisterRequest):
    try:
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM merchants WHERE email = ?", (register_data.email,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Invalid input")
        hashed_password = pwd_context.hash(register_data.password)
        cursor.execute("INSERT INTO merchants (email, name, hashed_password) VALUES (?, ?, ?)",
                       (register_data.email, register_data.name, hashed_password))
        conn.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    finally:
        try:
            conn.close()
        except:
            pass
    return {"message": "Merchant registered successfully"}

@app.post("/merchants/login", timeout=30)
async def login_merchant(login_data: LoginRequest, response: Response):
    try:
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM merchants WHERE email = ?", (login_data.email,))
        merchant = cursor.fetchone()
        if not merchant or not pwd_context.verify(login_data.password, merchant[2]):
            raise HTTPException(
                status_code=401, 
                detail={"error": "Invalid email or password"}
            )
        access_token = create_access_token(
            data={"sub": merchant[0]}, 
            expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        )
        response.set_cookie(
            key="AUTH_COOKIE", 
            value=access_token, 
            httponly=True, 
            secure=True
        )
    finally:
        try:
            conn.close()
        except:
            pass
    return {"message": "Login successful"}

@app.post("/merchants/upload-wares", timeout=30)
async def upload_wares(upload_data: UploadWaresRequest, current_email: str = Depends(get_current_merchant)):
    try:
        if len(upload_data.csv) > 1024 * 1024:  # 1MB limit
            raise HTTPException(status_code=400, detail="CSV too large")
        
        csv_file = io.StringIO(upload_data.csv)
        csv_reader = csv.DictReader(csv_file)
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        
        row_count = 0
        for row in csv_reader:
            row_count += 1
            if row_count > 100:  # Limit to 100 rows
                raise HTTPException(status_code=400, detail="CSV contains too many rows (max 100 allowed)")
                
            if not all(k in row for k in ['name', 'description', 'price']):
                raise ValueError("Missing required columns")
                
            try:
                price = float(row['price'].strip())
                if price <= 0:
                    raise ValueError("Price must be positive")
            except ValueError as ve:
                raise HTTPException(status_code=400, detail=f"Invalid price format: {str(ve)}")
                
            cursor.execute(
                "INSERT INTO wares (name, description, price, merchant_email) VALUES (?, ?, ?, ?)",
                (row['name'].strip(), row['description'].strip(), price, current_email)
            )
            
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=f"Invalid CSV format: {str(e)}")
    finally:
        try:
            conn.close()
        except:
            pass
    return {"message": "Wares uploaded successfully"}

@app.get("/wares", timeout=30)
async def get_wares(
    minPrice: Optional[float] = None,
    maxPrice: Optional[float] = None,
    current_email: str = Depends(get_current_merchant)
):
    try:
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        query = "SELECT w.name, w.description, w.price, m.email FROM wares w JOIN merchants m ON w.merchant_email = m.email"
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
        query += " LIMIT 1000"  # Prevent excessive result set size
        
        cursor.execute(query, params)
        wares = cursor.fetchall()
        
        # Use list for efficient HTML generation
        html_parts = ['<table border="1"><tr><th>Name</th><th>Description</th><th>Price</th><th>Merchant Email</th></tr>']
        for name, description, price, email in wares:
            html_parts.append(f'<tr><td>{name}</td><td>{description}</td><td>{price}</td><td>{email}</td></tr>')
        html_parts.append('</table>')
        
        return HTMLResponse(content=''.join(html_parts), status_code=200)
    finally:
        try:
            conn.close()
        except:
            pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000, timeout_keep_alive=30)