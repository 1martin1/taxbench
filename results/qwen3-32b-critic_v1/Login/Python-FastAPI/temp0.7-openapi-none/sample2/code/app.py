import os
import sqlite3
import time
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from email_validator import validate_email, EmailNotValidError
from passlib.hash import bcrypt
import jwt
from datetime import datetime, timedelta

app = FastAPI()

# Rate limiting for login attempts
LOGIN_ATTEMPTS = {}

def rate_limit(email: str):
    now = time.time()
    # Remove old attempts (older than 1 minute)
    LOGIN_ATTEMPTS[email] = [t for t in LOGIN_ATTEMPTS.get(email, []) if now - t < 60]
    if len(LOGIN_ATTEMPTS[email]) >= 5:
        raise HTTPException(status_code=429, detail="Too many attempts. Please try again later.")
    # Add the current attempt
    LOGIN_ATTEMPTS[email].append(now)

@app.on_event("startup")
def create_db():
    conn = None
    try:
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                name TEXT NOT NULL
            )
        ''')
        conn.commit()
    except sqlite3.Error as e:
        pass  # In production, log the error
    finally:
        if conn:
            conn.close()

class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str = Field(..., max_length=100)  # Input validation for name length

class LoginRequest(BaseModel):
    email: str
    password: str

class TokenResponse(BaseModel):
    token: str
    message: str

class ErrorResponse(BaseModel):
    message: str

@app.post("/register", status_code=201, responses={400: {"model": ErrorResponse}})
async def register(user: RegisterRequest):
    try:
        validate_email(user.email)
    except EmailNotValidError:
        raise HTTPException(status_code=400, detail={"message": "Registration failed. Please check your details."})
    
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM users WHERE email = ?", (user.email,))
        existing_user = cursor.fetchone()
        if existing_user:
            raise HTTPException(status_code=400, detail={"message": "Registration failed. Please check your details."})
        password_hash = bcrypt.hash(user.password)
        cursor.execute("INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)", 
                       (user.email, password_hash, user.name))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise HTTPException(status_code=400, detail={"message": "Registration failed. Please check your details."})
    finally:
        conn.close()
    return TokenResponse(token="", message="Registration successful")

@app.post("/login", responses={401: {"model": ErrorResponse}})
async def login(credentials: LoginRequest):
    rate_limit(credentials.email)
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute("SELECT password_hash FROM users WHERE email = ?", (credentials.email,))
    user = cursor.fetchone()
    conn.close()
    
    if not user:
        LOGIN_ATTEMPTS[credentials.email].append(time.time())
        raise HTTPException(status_code=401, detail={"message": "Invalid email or password"})
    
    password_hash = user[0]
    if not bcrypt.verify(credentials.password, password_hash):
        LOGIN_ATTEMPTS[credentials.email].append(time.time())
        raise HTTPException(status_code=401, detail={"message": "Invalid email or password"})
    
    # Reset login attempts on successful login
    LOGIN_ATTEMPTS[credentials.email] = []
    
    payload = {
        "sub": credentials.email,
        "exp": datetime.utcnow() + timedelta(hours=1)
    }
    try:
        token = jwt.encode(payload, os.getenv("APP_SECRET"), algorithm="HS256")
    except jwt.PyJWTError:
        raise HTTPException(status_code=500, detail="Internal Server Error")
    
    return TokenResponse(token=token, message="Login successful")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)