import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field, ValidationError

# -------------------- Configuration --------------------
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError("APP_SECRET environment variable is required")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# -------------------- Database --------------------
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
            hashed_password TEXT NOT NULL
        )
    """)
    # Secrets table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS secrets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            secret TEXT NOT NULL,
            FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()

init_db()

# -------------------- Security --------------------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, APP_SECRET, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)):
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return username
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

# -------------------- Pydantic Schemas --------------------
class RegisterRequest(BaseModel):
    email: EmailStr = Field(..., example="newuser@example.com")
    username: str = Field(..., example="user1")
    password: str = Field(..., min_length=6, example="newUserPassword")

class LoginRequest(BaseModel):
    email: EmailStr = Field(..., example="user@example.com")
    password: str = Field(..., example="mySecretPassword")

class SetSecretRequest(BaseModel):
    username: str = Field(..., example="user1")
    secret: str = Field(..., example="mySecret")

# -------------------- FastAPI App --------------------
app = FastAPI(
    title="Secret Storage",
    description="A secure cloud storage app for string secrets.",
    version="1.0.0",
)

# -------------------- Endpoints --------------------
@app.post("/register", status_code=201, responses={
    201: {"description": "Successful registration"},
    400: {"description": "Bad Request"},
})
def register(payload: RegisterRequest):
    conn = get_db()
    cur = conn.cursor()
    # Check if email or username already exists
    cur.execute("SELECT id FROM users WHERE email = ? OR username = ?", (payload.email, payload.username))
    if cur.fetchone():
        conn.close()
        raise HTTPException(
            status_code=400,
            detail="Email already in use or invalid data"
        )
    hashed_pw = get_password_hash(payload.password)
    cur.execute(
        "INSERT INTO users (email, username, hashed_password) VALUES (?, ?, ?)",
        (payload.email, payload.username, hashed_pw)
    )
    conn.commit()
    conn.close()
    return {"message": "Registration successful"}

@app.post("/login", responses={
    200: {"description": "Successful login"},
    401: {"description": "Unauthorized"},
})
def login(payload: LoginRequest):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT username, hashed_password FROM users WHERE email = ?", (payload.email,))
    row = cur.fetchone()
    conn.close()
    if not row or not verify_password(payload.password, row["hashed_password"]):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password"
        )
    access_token = create_access_token(data={"sub": row["username"]})
    return {"token": access_token, "message": "Login successful"}

@app.post("/set_secret", responses={
    200: {"description": "Secret set successfully"},
    401: {"description": "Unauthorized"},
})
def set_secret(request: SetSecretRequest, current_user: str = Depends(get_current_user)):
    # Ensure the username in payload matches the authenticated user
    if request.username != current_user:
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication token"
        )
    conn = get_db()
    cur = conn.cursor()
    # Upsert secret
    cur.execute("SELECT id FROM secrets WHERE username = ?", (request.username,))
    if cur.fetchone():
        cur.execute(
            "UPDATE secrets SET secret = ? WHERE username = ?",
            (request.secret, request.username)
        )
    else:
        cur.execute(
            "INSERT INTO secrets (username, secret) VALUES (?, ?)",
            (request.username, request.secret)
        )
    conn.commit()
    conn.close()
    return {"message": "Secret has been set successfully"}

@app.get("/get_secret", responses={
    200: {"description": "Secret retrieved successfully"},
    401: {"description": "Unauthorized"},
})
def get_secret(username: str, current_user: str = Depends(get_current_user)):
    # Users can only retrieve their own secret
    if username != current_user:
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication token"
        )
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT secret FROM secrets WHERE username = ?", (username,))
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(
            status_code=404,
            detail="Secret not found"
        )
    return {"secret": row["secret"]}

# -------------------- Run Server --------------------
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)