import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, status, Header, Query, Body
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field, ValidationError
from passlib.context import CryptContext
import jwt

# Load environment variables (APP_SECRET)
APP_SECRET = os.getenv("APP_SECRET", "default_secret_key_change_me")

# JWT settings
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 1 day

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# FastAPI app
app = FastAPI(title="Secret Storage", description="A secure cloud storage app for string secrets.", version="1.0.0")

# Security scheme
bearer_scheme = HTTPBearer(auto_error=False)

# Database setup
DB_PATH = "db.sqlite3"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    # Users table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
    """)
    # Secrets table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS secrets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            secret TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()

init_db()

# Pydantic models
class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)

class RegisterResponse(BaseModel):
    message: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)

class LoginResponse(BaseModel):
    token: str
    message: str

class SetSecretRequest(BaseModel):
    username: str
    secret: str

class SetSecretResponse(BaseModel):
    message: str

class GetSecretResponse(BaseModel):
    secret: str

class MessageResponse(BaseModel):
    message: str

# Helper functions
def get_user_by_email(email: str) -> Optional[sqlite3.Row]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE email = ?", (email,))
    user = cur.fetchone()
    conn.close()
    return user

def get_user_by_username(username: str) -> Optional[sqlite3.Row]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = ?", (username,))
    user = cur.fetchone()
    conn.close()
    return user

def create_user(email: str, username: str, password: str) -> sqlite3.Row:
    password_hash = pwd_context.hash(password)
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (email, username, password_hash) VALUES (?, ?, ?)",
            (email, username, password_hash),
        )
        conn.commit()
        user_id = cur.lastrowid
    except sqlite3.IntegrityError as e:
        conn.close()
        raise HTTPException(status_code=400, detail="Email or username already in use")
    conn.close()
    return get_user_by_username(username)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, APP_SECRET, algorithm=ALGORITHM)
    return encoded_jwt

def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid authentication token")

def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)):
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = credentials.credentials
    payload = decode_access_token(token)
    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    user = cur.fetchone()
    conn.close()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user

def set_user_secret(user_id: int, secret: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM secrets WHERE user_id = ?", (user_id,))
    existing = cur.fetchone()
    if existing:
        cur.execute("UPDATE secrets SET secret = ? WHERE user_id = ?", (secret, user_id))
    else:
        cur.execute("INSERT INTO secrets (user_id, secret) VALUES (?, ?)", (user_id, secret))
    conn.commit()
    conn.close()

def get_user_secret(user_id: int) -> Optional[str]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT secret FROM secrets WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row["secret"] if row else None

# Endpoints
@app.post("/register", response_model=RegisterResponse, status_code=201, responses={400: {"model": MessageResponse}})
def register(payload: RegisterRequest):
    if get_user_by_email(payload.email) or get_user_by_username(payload.username):
        raise HTTPException(status_code=400, detail="Email already in use or invalid data")
    create_user(payload.email, payload.username, payload.password)
    return {"message": "Registration successful"}

@app.post("/login", response_model=LoginResponse, responses={401: {"model": MessageResponse}})
def login(payload: LoginRequest):
    user = get_user_by_email(payload.email)
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token_data = {"sub": user["id"], "username": user["username"]}
    token = create_access_token(token_data)
    return {"token": token, "message": "Login successful"}

@app.post("/set_secret", response_model=SetSecretResponse, responses={401: {"model": MessageResponse}})
def set_secret(
    payload: SetSecretRequest,
    current_user: sqlite3.Row = Depends(get_current_user)
):
    # Ensure the token belongs to the username being set
    if current_user["username"] != payload.username:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    set_user_secret(current_user["id"], payload.secret)
    return {"message": "Secret has been set successfully"}

@app.get("/get_secret", response_model=GetSecretResponse, responses={401: {"model": MessageResponse}})
def get_secret(
    username: str = Query(..., description="Username to retrieve secret for"),
    current_user: sqlite3.Row = Depends(get_current_user)
):
    if current_user["username"] != username:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    secret = get_user_secret(current_user["id"])
    if secret is None:
        raise HTTPException(status_code=404, detail="Secret not found")
    return {"secret": secret}

# Run the application
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)