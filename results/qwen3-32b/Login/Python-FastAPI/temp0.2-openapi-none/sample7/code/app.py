import os
import sqlite3
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
from jose import jwt
from datetime import datetime, timedelta
import uvicorn

app = FastAPI(
    title="LoginAPI",
    description="A simple webapp API for login and registration functionality.",
    version="1.0.11"
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

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

class RegisterResponse(BaseModel):
    message: str

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

def get_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = app.openapi()
    openapi_schema["components"]["securitySchemes"] = {
        "bearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT"
        }
    }
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = get_openapi

@app.post("/register", response_model=RegisterResponse, status_code=201, tags=["Authentication"])
async def register(user: UserRegister):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ?", (user.email,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail={"message": "Email already in use or invalid data"})
    
    hashed_password = pwd_context.hash(user.password)
    cursor.execute("INSERT INTO users (email, password, name) VALUES (?, ?, ?)", 
                   (user.email, hashed_password, user.name))
    conn.commit()
    conn.close()
    return {"message": "Registration successful"}

@app.post("/login", response_model=TokenResponse, tags=["Authentication"])
async def login(user: UserLogin):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ?", (user.email,))
    db_user = cursor.fetchone()
    conn.close()
    
    if not db_user:
        raise HTTPException(status_code=401, detail={"message": "Invalid email or password"})
    
    if not pwd_context.verify(user.password, db_user[1]):
        raise HTTPException(status_code=401, detail={"message": "Invalid email or password"})
    
    payload = {
        "sub": user.email,
        "exp": datetime.utcnow() + timedelta(hours=1)
    }
    token = jwt.encode(payload, os.environ["APP_SECRET"], algorithm="HS256")
    return {"token": token, "message": "Login successful"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)