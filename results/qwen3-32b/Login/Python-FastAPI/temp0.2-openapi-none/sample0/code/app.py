from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, EmailStr
import sqlite3
import os
from datetime import datetime, timedelta
import jwt
from passlib.context import CryptContext

app = FastAPI()

# Database initialization
def init_db():
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            hashed_password TEXT NOT NULL,
            name TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

# Create the database table on startup
@app.on_event("startup")
def on_startup():
    init_db()

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Pydantic models
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    name: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class LoginResponse(BaseModel):
    token: str
    message: str

class RegisterResponse(BaseModel):
    message: str

# Helper functions
def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: timedelta = None):
    secret = os.getenv("APP_SECRET")
    if not secret:
        raise ValueError("APP_SECRET environment variable not set")
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, secret, algorithm="HS256")
    return encoded_jwt

# Endpoints
@app.post("/register", response_model=RegisterResponse, status_code=201)
def register_user(user: RegisterRequest):
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM users WHERE email = ?", (user.email,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Email already in use or invalid data")
        
        hashed_password = hash_password(user.password)
        cursor.execute(
            "INSERT INTO users (email, hashed_password, name) VALUES (?, ?, ?)",
            (user.email, hashed_password, user.name)
        )
        conn.commit()
        return {"message": "Registration successful"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Email already in use or invalid data")
    finally:
        conn.close()

@app.post("/login", response_model=LoginResponse)
def login_user(credentials: LoginRequest):
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute("SELECT hashed_password FROM users WHERE email = ?", (credentials.email,))
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    hashed_password = result[0]
    if not verify_password(credentials.password, hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    token = create_access_token(data={"sub": credentials.email})
    return {"token": token, "message": "Login successful"}

# Run the server
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)