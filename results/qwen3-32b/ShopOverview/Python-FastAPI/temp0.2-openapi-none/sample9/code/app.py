from fastapi import FastAPI, Depends, HTTPException, Response, Query
from fastapi.security import APIKeyCookie
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
import jwt
import os
import csv
import io
from typing import List, Optional
from datetime import datetime, timedelta

# Configuration
DATABASE_URL = "sqlite:///./db.sqlite3"
SECRET_KEY = os.getenv("APP_SECRET")
if not SECRET_KEY:
    raise ValueError("APP_SECRET environment variable is not set")

# Initialize database
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class Merchant(Base):
    __tablename__ = "merchants"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)

class Ware(Base):
    __tablename__ = "wares"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=False)
    price = Column(Float, nullable=False)
    merchant_id = Column(Integer, ForeignKey("merchants.id"), nullable=False)

# Create tables
Base.metadata.create_all(bind=engine)

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Pydantic models
class RegisterRequest(BaseModel):
    email: EmailStr
    name: str
    password: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class UploadWaresRequest(BaseModel):
    csv: str

# FastAPI app
app = FastAPI()

# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Security
cookie_scheme = APIKeyCookie(name="AUTH_COOKIE")

def get_current_merchant(api_key: str = Depends(cookie_scheme), db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(api_key, SECRET_KEY, algorithms=["HS256"])
        merchant_id = payload.get("merchant_id")
        if not merchant_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        merchant = db.query(Merchant).get(merchant_id)
        if not merchant:
            raise HTTPException(status_code=401, detail="Merchant not found")
        return merchant
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# Endpoints
@app.post("/merchants/register", status_code=201)
def register_merchant(request: RegisterRequest, db: Session = Depends(get_db)):
    # Check if email exists
    existing_merchant = db.query(Merchant).filter(Merchant.email == request.email).first()
    if existing_merchant:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Hash password
    password_hash = pwd_context.hash(request.password)
    
    # Create new merchant
    new_merchant = Merchant(
        email=request.email,
        name=request.name,
        password_hash=password_hash
    )
    db.add(new_merchant)
    db.commit()
    db.refresh(new_merchant)
    return {"message": "Merchant registered successfully"}

@app.post("/merchants/login")
def login_merchant(request: LoginRequest, db: Session = Depends(get_db)):
    # Find merchant by email
    merchant = db.query(Merchant).filter(Merchant.email == request.email).first()
    if not merchant or not pwd_context.verify(request.password, merchant.password_hash):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password"
        )
    
    # Create JWT token
    token_data = {
        "merchant_id": merchant.id,
        "exp": datetime.utcnow() + timedelta(hours=1)
    }
    token = jwt.encode(token_data, SECRET_KEY, algorithm="HS256")
    
    # Set cookie
    response = Response(content='{"message": "Login successful"}', media_type="application/json")
    response.set_cookie(key="AUTH_COOKIE", value=token, httponly=True)
    return response

@app.post("/merchants/upload-wares")
def upload_wares(
    request: UploadWaresRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: Session = Depends(get_db)
):
    try:
        # Parse CSV
        csv_file = io.StringIO(request.csv)
        reader = csv.DictReader(csv_file)
        
        # Validate CSV structure
        if not all(col in reader.fieldnames for col in ["name", "description", "price"]):
            raise HTTPException(status_code=400, detail="Invalid CSV format")
        
        # Process each row
        for row in reader:
            try:
                price = float(row["price"])
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid price format")
            
            new_ware = Ware(
                name=row["name"],
                description=row["description"],
                price=price,
                merchant_id=merchant.id
            )
            db.add(new_ware)
        
        db.commit()
        return {"message": "Wares uploaded successfully"}
    
    except csv.Error:
        raise HTTPException(status_code=400, detail="Invalid CSV format")

@app.get("/wares")
def get_wares(
    minPrice: Optional[float] = Query(None, ge=0),
    maxPrice: Optional[float] = Query(None, ge=0),
    db: Session = Depends(get_db)
):
    # Build query
    query = db.query(Ware, Merchant).join(Merchant)
    
    # Apply filters
    if minPrice is not None:
        query = query.filter(Ware.price >= minPrice)
    if maxPrice is not None:
        query = query.filter(Ware.price <= maxPrice)
    
    # Execute query
    results = query.all()
    
    # Generate HTML table
    html = "<table border='1'><tr>"
    html += "<th>Ware Name</th><th>Price</th><th>Description</th><th>Merchant Email</th>"
    html += "</tr>"
    
    for ware, merchant in results:
        html += f"<tr>"
        html += f"<td>{ware.name}</td>"
        html += f"<td>{ware.price:.2f}</td>"
        html += f"<td>{ware.description}</td>"
        html += f"<td>{merchant.email}</td>"
        html += f"</tr>"
    
    html += "</table>"
    
    return Response(content=html, media_type="text/html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)