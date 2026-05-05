import os
import sqlite3
from fastapi import FastAPI, Depends, HTTPException, status, Query
from pydantic import BaseModel, EmailStr, field_validator
from passlib.context import CryptContext
from jose import JWTError, jwt
from datetime import datetime, timedelta
from dotenv import load_dotenv
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import re

load_dotenv()

app = FastAPI()

# Database setup
def init_db():
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS secrets (
            username TEXT PRIMARY KEY,
            secret TEXT NOT NULL,
            FOREIGN KEY (username) REFERENCES users (username) ON DELETE CASCADE
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT settings
SECRET_KEY = os.getenv("APP_SECRET")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Startup validation
@app.on_event("startup")
async def check_secret_key():
    if not SECRET_KEY:
        raise ValueError("APP_SECRET environment variable is not set")

# Models
class RegisterRequest(BaseModel):
    email: EmailStr
    username: str
    password: str

    @field_validator('password')
    def password_complexity(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters long')
        if not re.search(r'[A-Z]', v):
            raise ValueError('Password must contain at least one uppercase letter')
        if not re.search(r'[a-z]', v):
            raise ValueError('Password must contain at least one lowercase letter')
        if not re.search(r'[0-9]', v):
            raise ValueError('Password must contain at least one digit')
        return v

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class TokenResponse(BaseModel):
    token: str
    message: str

class SetSecretRequest(BaseModel):
    username: str
    secret: str

# Utility functions
def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# Authentication dependency
security_bearer = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security_bearer)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token")
        return username
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token")

# Endpoints
@app.post("/register", status_code=201)
async def register(request: RegisterRequest):
    hashed_password = pwd_context.hash(request.password)
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO users (email, username, password_hash) VALUES (?, ?, ?)",
                       (request.email, request.username, hashed_password))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid data")
    conn.close()
    return {"message": "Registration successful"}

@app.post("/login")
async def login(request: LoginRequest):
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute("SELECT username, password_hash FROM users WHERE email = ?", (request.email,))
    user = cursor.fetchone()
    conn.close()
    if not user or not pwd_context.verify(request.password, user[1]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    token = create_access_token(
        data={"sub": user[0], "email": request.email},
        expires_delta=access_token_expires
    )
    return {"token": token, "message": "Login successful"}

@app.post("/set_secret")
async def set_secret(request: SetSecretRequest, current_user: str = Depends(get_current_user)):
    if request.username != current_user:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT OR REPLACE INTO secrets (username, secret) VALUES (?, ?)",
                       (request.username, request.secret))
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail="Database error")
    conn.close()
    return {"message": "Secret has been set successfully"}

@app.get("/get_secret")
async def get_secret(username: str = Query(...), current_user: str = Depends(get_current_user)):
    if username != current_user:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute("SELECT secret FROM secrets WHERE username = ?", (username,))
    secret_row = cursor.fetchone()
    conn.close()
    if not secret_row:
        raise HTTPException(status_code=404, detail="Secret not found")
    return {"secret": secret_row[0]}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)