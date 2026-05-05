from fastapi import FastAPI, HTTPException
import sqlite3
from pydantic import BaseModel, EmailStr, Field
from passlib.context import CryptContext
import jwt
import os
import datetime

app = FastAPI()

# Database initialization
def init_db():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            name TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

@app.on_event("startup")
def on_startup():
    init_db()

# Password hashing setup
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Pydantic models
class LoginRequest(BaseModel):
    email: EmailStr = Field(..., max_length=255)
    password: str = Field(..., max_length=128)

class RegisterRequest(LoginRequest):
    name: str = Field(..., max_length=255)

class LoginResponse(BaseModel):
    token: str
    message: str

class RegisterResponse(BaseModel):
    message: str

# Endpoints
@app.post("/register", response_model=RegisterResponse, status_code=201)
def register(user: RegisterRequest):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM users WHERE email = ?", (user.email,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Email already in use")
        
        hashed_password = pwd_context.hash(user.password)
        cursor.execute(
            "INSERT INTO users (email, password, name) VALUES (?, ?, ?)",
            (user.email, hashed_password, user.name)
        )
        conn.commit()
        return {"message": "Registration successful"}
    except sqlite3.IntegrityError:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Email already in use")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

@app.post("/login", response_model=LoginResponse)
def login(credentials: LoginRequest):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT password FROM users WHERE email = ?", (credentials.email,))
    result = cursor.fetchone()
    conn.close()
    
    if not result or not pwd_context.verify(credentials.password, result[0]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    payload = {
        "email": credentials.email,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1)
    }
    
    secret = os.environ.get("APP_SECRET")
    if not secret:
        raise HTTPException(status_code=500, detail="Configuration error: APP_SECRET not set")
    try:
        token = jwt.encode(payload, secret, algorithm="HS256")
        return {"token": token, "message": "Login successful"}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Could not create token")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)