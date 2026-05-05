from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr
import sqlite3
import os
from passlib.context import CryptContext
import jwt
from datetime import datetime, timedelta

app = FastAPI()

# Validate required environment variables
if not os.getenv("APP_SECRET"):
    raise ValueError("APP_SECRET environment variable is not set")

# Password hashing context
ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Database setup
def get_db():
    db = sqlite3.connect('db.sqlite3')
    db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            hashed_password TEXT NOT NULL,
            name TEXT NOT NULL
        )
    ''')
    db.commit()
    return db

@app.on_event("startup")
def on_startup():
    get_db()

# Models
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

# JWT functions
def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(hours=1)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, os.getenv("APP_SECRET"), algorithm="HS256")
    return encoded_jwt

# Endpoints
@app.post("/register", status_code=201)
def register(user: UserRegister):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ?", (user.email,))
    if cursor.fetchone():
        db.close()
        raise HTTPException(status_code=400, detail="Email already in use or invalid data")
    hashed = ctx.hash(user.password)
    try:
        cursor.execute("INSERT INTO users (email, hashed_password, name) VALUES (?, ?, ?)",
                       (user.email, hashed, user.name))
        db.commit()
    except Exception as e:
        db.close()
        raise HTTPException(status_code=400, detail="Email already in use or invalid data")
    finally:
        db.close()
    return {"message": "Registration successful"}

@app.post("/login")
def login(user: UserLogin):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ?", (user.email,))
    db_user = cursor.fetchone()
    db.close()
    if not db_user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not ctx.verify(user.password, db_user[2]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token({"sub": user.email})
    return TokenResponse(token=token, message="Login successful")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)