import os
import sqlite3
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr, StringConstraints, AfterValidator
from typing import Annotated
from passlib.context import CryptContext
import jwt
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# Validate APP_SECRET at startup
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise ValueError("APP_SECRET must be set in the environment")

app = FastAPI(title="LoginAPI", version="1.0.11")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def init_db():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            hashed_password TEXT NOT NULL,
            name TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

init_db()

# Name validation: 1-100 characters, letters, spaces, hyphens, apostrophes
NameStr = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z\s'\\-]+$"
    )
]

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    name: NameStr

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class TokenResponse(BaseModel):
    token: str
    message: str

@app.post("/register", status_code=201)
def register(user: UserCreate):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    try:
        hashed_password = pwd_context.hash(user.password)
        cursor.execute("INSERT INTO users (email, hashed_password, name) VALUES (?, ?, ?)",
                       (user.email, hashed_password, user.name))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Email already in use or invalid data")
    finally:
        conn.close()
    return {"message": "Registration successful"}

@app.post("/login")
def login(login_data: LoginRequest):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT hashed_password FROM users WHERE email = ?", (login_data.email,))
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    if not pwd_context.verify(login_data.password, result[0]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    token = jwt.encode(
        {
            "sub": login_data.email,
            "exp": datetime.utcnow() + timedelta(hours=1)
        },
        APP_SECRET,
        algorithm="HS256"
    )
    
    return {"token": token, "message": "Login successful"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)