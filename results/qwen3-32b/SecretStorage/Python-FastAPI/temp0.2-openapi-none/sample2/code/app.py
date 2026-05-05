import os
import sqlite3
from datetime import datetime, timedelta
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
import jwt

app = FastAPI()
security = HTTPBearer()

# Configuration
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise ValueError("APP_SECRET environment variable is not set")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Pydantic models
class RegisterRequest(BaseModel):
    email: EmailStr
    username: str
    password: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class SetSecretRequest(BaseModel):
    username: str
    secret: str

# Helper functions
def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, APP_SECRET, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        email: str = payload.get("email")
        if username is None or email is None:
            raise HTTPException(status_code=401, detail="Invalid authentication token")
        return {"username": username, "email": email}
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication token")

# Database setup
@app.on_event("startup")
async def startup():
    conn = sqlite3.connect("db.sqlite3")
    conn.execute("PRAGMA foreign_keys = ON")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS secrets (
            username TEXT PRIMARY KEY,
            secret TEXT NOT NULL,
            FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()

# Endpoints
@app.post("/register", status_code=201)
async def register(register_data: RegisterRequest):
    email = register_data.email
    username = register_data.username
    password = register_data.password

    password_hash = pwd_context.hash(password)
    try:
        conn = sqlite3.connect("db.sqlite3")
        conn.execute("PRAGMA foreign_keys = ON")
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users (email, username, password_hash) VALUES (?, ?, ?)", (email, username, password_hash))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Email already in use or invalid data")
    finally:
        conn.close()
    return {"message": "Registration successful"}

@app.post("/login")
async def login(login_data: LoginRequest):
    email = login_data.email
    password = login_data.password

    conn = sqlite3.connect("db.sqlite3")
    conn.execute("PRAGMA foreign_keys = ON")
    cursor = conn.cursor()
    cursor.execute("SELECT username, password_hash FROM users WHERE email = ?", (email,))
    user = cursor.fetchone()
    conn.close()

    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    username, password_hash = user

    if not pwd_context.verify(password, password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    access_token = create_access_token(data={"sub": username, "email": email})
    return {"token": access_token, "message": "Login successful"}

@app.post("/set_secret")
async def set_secret(
    data: SetSecretRequest,
    user: dict = Depends(verify_token)
):
    if data.username != user["username"]:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    
    conn = sqlite3.connect("db.sqlite3")
    conn.execute("PRAGMA foreign_keys = ON")
    cursor = conn.cursor()
    try:
        cursor.execute("REPLACE INTO secrets (username, secret) VALUES (?, ?)", (data.username, data.secret))
        conn.commit()
    finally:
        conn.close()
    
    return {"message": "Secret has been set successfully"}

@app.get("/get_secret")
async def get_secret(
    username: str,
    user: dict = Depends(verify_token)
):
    if username != user["username"]:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    
    conn = sqlite3.connect("db.sqlite3")
    conn.execute("PRAGMA foreign_keys = ON")
    cursor = conn.cursor()
    cursor.execute("SELECT secret FROM secrets WHERE username = ?", (username,))
    secret_row = cursor.fetchone()
    conn.close()

    if secret_row:
        return {"secret": secret_row[0]}
    else:
        return {"secret": ""}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)