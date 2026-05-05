import os
import jwt
import csv
import io
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, status, Response, Query
from fastapi.security import APIKeyCookie
from pydantic import BaseModel, EmailStr, Field
from passlib.context import CryptContext
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime, timedelta
from jinja2 import Template

app = FastAPI()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
cookie_sec = APIKeyCookie(name="AUTH_COOKIE", auto_error=False)

# Database setup
SQLALCHEMY_DATABASE_URL = "sqlite:///./db.sqlite3"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class Merchant(Base):
    __tablename__ = "merchants"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, index=True)
    name = Column(String)
    password_hash = Column(String)

class Ware(Base):
    __tablename__ = "wares"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    description = Column(String)
    price = Column(Float)
    merchant_email = Column(String, ForeignKey("merchants.email"))

Base.metadata.create_all(bind=engine)

# Schemas
class MerchantCreate(BaseModel):
    email: EmailStr
    name: str
    password: str

class WareCreate(BaseModel):
    csv: str

class WareResponse(BaseModel):
    id: int
    name: str
    description: str
    price: float
    merchant_email: str

# Dependencies
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(hours=1)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, os.getenv("APP_SECRET"), algorithm="HS256")

def get_current_merchant(cookie: str = Depends(cookie_sec)) -> str:
    if not cookie:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    try:
        payload = jwt.decode(cookie, os.getenv("APP_SECRET"), algorithms=["HS256"])
        return payload.get("sub")
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "ApiKey"},
        )

# Routes
@app.post("/merchants/register", status_code=201)
def register_merchant(merchant: MerchantCreate, db: Session = Depends(get_db)):
    db_merchant = db.query(Merchant).filter(Merchant.email == merchant.email).first()
    if db_merchant:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    db_merchant = Merchant(
        email=merchant.email,
        name=merchant.name,
        password_hash=get_password_hash(merchant.password)
    )
    db.add(db_merchant)
    db.commit()
    db.refresh(db_merchant)
    return {"message": "Merchant registered successfully"}

@app.post("/merchants/login")
def login_merchant(email: str, password: str, db: Session = Depends(get_db), response: Response = None):
    merchant = db.query(Merchant).filter(Merchant.email == email).first()
    if not merchant or not verify_password(password, merchant.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid email or password"}
        )
    
    access_token = create_access_token(data={"sub": merchant.email})
    response.set_cookie(key="AUTH_COOKIE", value=access_token, httponly=True)
    return {"message": "Login successful"}

@app.post("/merchants/upload-wares")
def upload_wares(wares: WareCreate, merchant_email: str = Depends(get_current_merchant), db: Session = Depends(get_db)):
    MAX_CSV_SIZE = 1024 * 1024  # 1MB
    if len(wares.csv) > MAX_CSV_SIZE:
        raise HTTPException(status_code=400, detail="CSV too large")
    
    try:
        csv_data = io.StringIO(wares.csv)
        reader = csv.reader(csv_data)
        header = next(reader)
        if len(header) != 3 or header[0].lower() != "name" or header[1].lower() != "description" or header[2].lower() != "price":
            raise HTTPException(status_code=400, detail="Invalid CSV format")
        
        for row in reader:
            if len(row) != 3:
                raise HTTPException(status_code=400, detail="Invalid CSV format")
            name, description, price = row
            try:
                price = float(price)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid price format")
            
            db_ware = Ware(
                name=name.strip(),
                description=description.strip(),
                price=price,
                merchant_email=merchant_email
            )
            db.add(db_ware)
    except csv.Error:
        raise HTTPException(status_code=400, detail="Invalid CSV format")
    
    db.commit()
    return {"message": "Wares uploaded successfully"}

@app.get("/wares")
def get_wares(
    minPrice: Optional[float] = Query(None),
    maxPrice: Optional[float] = Query(None),
    db: Session = Depends(get_db)
):
    query = db.query(Ware).join(Merchant, Ware.merchant_email == Merchant.email)
    
    if minPrice is not None:
        query = query.filter(Ware.price >= minPrice)
    if maxPrice is not None:
        query = query.filter(Ware.price <= maxPrice)
    
    # Limit results to prevent excessive memory usage
    results = query.limit(100).all()
    
    html = """
    <html>
    <body>
        <h1>Available Wares</h1>
        <table border="1">
            <tr>
                <th>Name</th>
                <th>Description</th>
                <th>Price</th>
                <th>Merchant</th>
            </tr>
            {% for item in items %}
            <tr>
                <td>{{ item.name|e }}</td>
                <td>{{ item.description|e }}</td>
                <td>{{ "%.2f" % item.price }}</td>
                <td>{{ item.merchant_email|e }}</td>
            </tr>
            {% endfor %}
        </table>
    </body>
    </html>
    """
    
    template = Template(html)
    return Response(content=template.render(items=results), media_type="text/html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)