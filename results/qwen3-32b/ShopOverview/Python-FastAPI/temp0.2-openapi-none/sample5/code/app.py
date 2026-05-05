import os
import csv
import jwt
import uuid
from fastapi import FastAPI, Depends, HTTPException, Response, Query
from fastapi.security import APIKeyCookie
from pydantic import BaseModel, EmailStr, Field
from typing import List, Optional
from datetime import datetime, timedelta
from sqlalchemy import create_engine, Column, String, Float, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from jinja2 import Template

# Database setup
DATABASE_URL = "sqlite:///./db.sqlite3"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class Merchant(Base):
    __tablename__ = "merchants"
    email = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)

class Ware(Base):
    __tablename__ = "wares"
    id = Column(String, primary_key=True, default=str(uuid.uuid4()))
    name = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    price = Column(Float, nullable=False)
    merchant_email = Column(String, ForeignKey("merchants.email"), nullable=False)

Base.metadata.create_all(bind=engine)

# Security
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise ValueError("APP_SECRET environment variable not set")

cookie_scheme = APIKeyCookie(name="AUTH_COOKIE", auto_error=False)

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
    name: str
    description: str
    price: float
    merchant_email: str

# FastAPI app
app = FastAPI()

# Password hashing
from passlib.context import CryptContext
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: timedelta):
    to_encode = data.copy()
    expire = datetime.utcnow() + expires_delta
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, APP_SECRET, algorithm="HS256")
    return encoded_jwt

def get_current_merchant(cookie: str = Depends(cookie_scheme)):
    if not cookie:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        payload = jwt.decode(cookie, APP_SECRET, algorithms=["HS256"])
        return payload["sub"]
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Unauthorized")

# HTML Template with XSS protection
HTML_TEMPLATE = Template("""
<!DOCTYPE html>
<html>
<head>
    <title>Wares</title>
</head>
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
            <td>{{ ware.name|e }}</td>
            <td>{{ ware.description|e }}</td>
            <td>{{ ware.price|e }}</td>
            <td>{{ ware.merchant_email|e }}</td>
        </tr>
        {% endfor %}
    </table>
</body>
</html>
""")

@app.post("/merchants/register", status_code=201)
def register_merchant(request: RegisterRequest):
    db = SessionLocal()
    try:
        existing = db.query(Merchant).filter(Merchant.email == request.email).first()
        if existing:
            raise HTTPException(status_code=400, detail="Email already registered")
        
        merchant = Merchant(
            email=request.email,
            name=request.name,
            password_hash=get_password_hash(request.password)
        )
        db.add(merchant)
        db.commit()
    finally:
        db.close()
    return {"message": "Merchant registered successfully"}

@app.post("/merchants/login")
def login_merchant(request: LoginRequest, response: Response):
    db = SessionLocal()
    try:
        merchant = db.query(Merchant).filter(Merchant.email == request.email).first()
        if not merchant or not verify_password(request.password, merchant.password_hash):
            raise HTTPException(status_code=401, detail={"error": "Invalid email or password"})
        
        access_token = create_access_token(
            data={"sub": merchant.email},
            expires_delta=timedelta(hours=1)
        )
        response.set_cookie(
            key="AUTH_COOKIE", 
            value=access_token, 
            secure=True, 
            httponly=True, 
            samesite='lax'
        )
        return {"message": "Login successful"}
    finally:
        db.close()

@app.post("/merchants/upload-wares")
def upload_wares(request: WareUploadRequest, current_merchant: str = Depends(get_current_merchant)):
    db = SessionLocal()
    try:
        csv_data = request.csv.strip().split('\n')
        reader = csv.reader(csv_data)
        
        for row in reader:
            if len(row) != 3:
                raise HTTPException(status_code=400, detail="Invalid CSV format")
            name, description, price = row
            try:
                price_float = float(price)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid price format")
            
            ware = Ware(
                name=name,
                description=description,
                price=price_float,
                merchant_email=current_merchant
            )
            db.add(ware)
        
        db.commit()
        return {"message": "Wares uploaded successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail="Invalid CSV format")
    finally:
        db.close()

@app.get("/wares")
def get_wares(minPrice: Optional[float] = Query(None), maxPrice: Optional[float] = Query(None)):
    db = SessionLocal()
    try:
        query = db.query(Ware).join(Merchant)
        
        if minPrice is not None:
            query = query.filter(Ware.price >= minPrice)
        if maxPrice is not None:
            query = query.filter(Ware.price <= maxPrice)
        
        results = query.all()
        wares = [{
            "name": w.name,
            "description": w.description,
            "price": w.price,
            "merchant_email": w.merchant_email
        } for w in results]
        
        html_content = HTML_TEMPLATE.render(wares=wares)
        return Response(content=html_content, media_type="text/html")
    finally:
        db.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)