import os
import sqlite3
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
import jwt
from datetime import datetime, timedelta

# Initialize password context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Initialize FastAPI app
app = FastAPI()

# Create database and tables on startup
@app.on_event("startup")
def create_tables():
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

# Pydantic models
class UserRegister(BaseModel):
    email: EmailStr
    password: str
    name: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class TokenResponse(BaseModel):
    token: str
    message: str

# Registration endpoint
@app.post("/register", status_code=201)
def register(user: UserRegister):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    # Check if email exists
    cursor.execute("SELECT * FROM users WHERE email = ?", (user.email,))
    existing_user = cursor.fetchone()
    if existing_user:
        conn.close()
        raise HTTPException(status_code=400, detail="Email already in use or invalid data")
    
    # Hash password
    hashed_password = pwd_context.hash(user.password)
    
    # Insert new user
    cursor.execute(
        "INSERT INTO users (email, password, name) VALUES (?, ?, ?)",
        (user.email, hashed_password, user.name)
    )
    conn.commit()
    conn.close()
    return {"message": "Registration successful"}

# Login endpoint
@app.post("/login")
def login(user: UserLogin):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    # Fetch stored password
    cursor.execute("SELECT password FROM users WHERE email = ?", (user.email,))
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    stored_hash = result[0]
    if not pwd_context.verify(user.password, stored_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    # Generate JWT token
    secret_key = os.getenv("APP_SECRET")
    if not secret_key:
        raise HTTPException(status_code=500, detail="Server configuration error")
    
    token_data = {
        "email": user.email,
        "exp": datetime.utcnow() + timedelta(minutes=30)
    }
    token = jwt.encode(token_data, secret_key, algorithm="HS256")
    return {"token": token, "message": "Login successful"}

# Run the app
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)