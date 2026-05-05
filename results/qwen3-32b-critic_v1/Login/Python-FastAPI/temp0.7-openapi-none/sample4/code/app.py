from fastapi import FastAPI, HTTPException, status, Depends, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import create_engine, Column, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from passlib.context import CryptContext
import jwt
import os
from datetime import datetime, timedelta
import threading

# Database setup
SQLALCHEMY_DATABASE_URL = "sqlite:///./db.sqlite3"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# User model
class User(Base):
    __tablename__ = "users"
    email = Column(String, primary_key=True)
    hashed_password = Column(String)
    name = Column(String)

# Pydantic models
class RegisterRequest(BaseModel):
    email: EmailStr = Field(..., max_length=255)
    password: str = Field(..., max_length=128)
    name: str = Field(..., max_length=100)

class LoginRequest(BaseModel):
    email: EmailStr = Field(..., max_length=255)
    password: str = Field(..., max_length=128)

class RegisterResponse(BaseModel):
    message: str

class LoginResponse(BaseModel):
    token: str
    message: str

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Rate limiting
class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests = {}
        self.lock = threading.Lock()

    def __call__(self, request: Request):
        client_ip = request.client.host
        now = datetime.utcnow()
        with self.lock:
            if client_ip not in self.requests:
                self.requests[client_ip] = []
            # Prune old requests
            self.requests[client_ip] = [t for t in self.requests[client_ip] if (now - t).total_seconds() < self.window_seconds]
            if len(self.requests[client_ip]) >= self.max_requests:
                raise HTTPException(status_code=429, detail="Too many requests, please try again later.")
            self.requests[client_ip].append(now)

# FastAPI app
app = FastAPI()

# Rate limiter instances
login_limiter = RateLimiter(max_requests=5, window_seconds=60)
register_limiter = RateLimiter(max_requests=5, window_seconds=60)

# Startup validation
@app.on_event("startup")
def check_app_secret_and_init_db():
    # Check APP_SECRET
    app_secret = os.getenv("APP_SECRET")
    if not app_secret:
        raise RuntimeError("APP_SECRET environment variable is not set")
    # Initialize database
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as e:
        raise RuntimeError(f"Failed to initialize database: {e}")

@app.post("/register", response_model=RegisterResponse, status_code=201, dependencies=[Depends(register_limiter)])
def register(user_data: RegisterRequest):
    db = SessionLocal()
    try:
        existing_user = db.query(User).filter(User.email == user_data.email).first()
        if existing_user:
            raise HTTPException(status_code=400, detail={"message": "Email already in use or invalid data"})
        hashed_password = pwd_context.hash(user_data.password)
        new_user = User(email=user_data.email, hashed_password=hashed_password, name=user_data.name)
        db.add(new_user)
        db.commit()
    except:
        db.rollback()
        raise
    finally:
        db.close()
    return {"message": "Registration successful"}

@app.post("/login", response_model=LoginResponse, dependencies=[Depends(login_limiter)])
def login(login_data: LoginRequest):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == login_data.email).first()
    finally:
        db.close()
    if not user or not pwd_context.verify(login_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail={"message": "Invalid email or password"})
    app_secret = os.getenv("APP_SECRET")
    payload = {
        "sub": user.email,
        "exp": datetime.utcnow() + timedelta(hours=1)
    }
    token = jwt.encode(payload, app_secret, algorithm="HS256")
    return {"token": token, "message": "Login successful"}

# Run the server
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)