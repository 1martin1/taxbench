import os
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Optional, List, Dict

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field, ValidationError

# ---------- Configuration ----------
APP_SECRET = os.getenv("APP_SECRET") or "fallback-secret-key-please-set-APP_SECRET"
DATABASE = "db.sqlite3"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
MAX_REQUEST_SIZE = 1 * 1024 * 1024  # 1 MB
RATE_LIMIT = 60  # requests
RATE_LIMIT_WINDOW = 60  # seconds

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)

# ---------- Database utilities ----------
def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS secrets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                secret TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()

# ---------- Pydantic models ----------
class RegisterRequest(BaseModel):
    email: EmailStr = Field(..., max_length=254)
    username: str = Field(..., min_length=1, max_length=150)
    password: str = Field(..., min_length=8, max_length=128)

class LoginRequest(BaseModel):
    email: EmailStr = Field(..., max_length=254)
    password: str = Field(..., min_length=8, max_length=128)

class SetSecretRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=150)
    secret: str = Field(..., min_length=1, max_length=1024)

class TokenResponse(BaseModel):
    token: str
    message: str = "Login successful"

class MessageResponse(BaseModel):
    message: str

class SecretResponse(BaseModel):
    secret: str

# ---------- FastAPI app ----------
app = FastAPI(title="Secret Storage", version="1.0.0")

# ---------- Middleware ----------
@app.middleware("http")
async def limit_request_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_REQUEST_SIZE:
        return HTTPException(status_code=413, detail="Payload too large")
    return await call_next(request)

_client_requests: Dict[str, List[float]] = {}

@app.middleware("http")
async def rate_limiter(request: Request, call_next):
    client_ip = request.client.host
    now = time.time()
    timestamps = _client_requests.get(client_ip, [])
    # Remove timestamps older than window
    timestamps = [ts for ts in timestamps if now - ts < RATE_LIMIT_WINDOW]
    timestamps.append(now)
    _client_requests[client_ip] = timestamps
    if len(timestamps) > RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many requests")
    return await call_next(request)

# ---------- Startup ----------
@app.on_event("startup")
def on_startup():
    init_db()

# ---------- Helper functions ----------
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, APP_SECRET, algorithm=ALGORITHM)
    return encoded_jwt

def encrypt_secret(secret: str) -> str:
    """Encrypt secret using JWT (symmetric)."""
    payload = {"secret": secret, "iat": datetime.utcnow()}
    return jwt.encode(payload, APP_SECRET, algorithm=ALGORITHM)

def decrypt_secret(token: str) -> str:
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=[ALGORITHM])
        return payload.get("secret", "")
    except JWTError:
        return ""

def get_user_by_email(email: str) -> Optional[sqlite3.Row]:
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE email = ?", (email,))
        return cur.fetchone()

def get_user_by_username(username: str) -> Optional[sqlite3.Row]:
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username = ?", (username,))
        return cur.fetchone()

def get_user_by_id(user_id: int) -> Optional[sqlite3.Row]:
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        return cur.fetchone()

def store_secret(user_id: int, secret: str):
    encrypted = encrypt_secret(secret)
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("INSERT OR REPLACE INTO secrets (user_id, secret) VALUES (?, ?)", (user_id, encrypted))
        conn.commit()

def retrieve_secret(user_id: int) -> Optional[str]:
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT secret FROM secrets WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        if row:
            return decrypt_secret(row["secret"])
        return None

# ---------- Dependency ----------
async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
):
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=[ALGORITHM])
        user_id: int = payload.get("sub")
        username: str = payload.get("username")
        if user_id is None or username is None:
            raise JWTError()
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = get_user_by_id(user_id)
    if user is None or user["username"] != username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user

# ---------- Routes ----------
@app.post("/register", response_model=MessageResponse, status_code=201)
def register(request: RegisterRequest):
    if get_user_by_email(request.email):
        raise HTTPException(status_code=400, detail="Email already in use or invalid data")
    if get_user_by_username(request.username):
        raise HTTPException(status_code=400, detail="Username already in use or invalid data")
    password_hash = get_password_hash(request.password)
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (email, username, password_hash) VALUES (?, ?, ?)",
            (request.email, request.username, password_hash),
        )
        conn.commit()
    return {"message": "Registration successful"}

@app.post("/login", response_model=TokenResponse)
def login(request: LoginRequest):
    user = get_user_by_email(request.email)
    if not user or not verify_password(request.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token_data = {"sub": user["id"], "username": user["username"]}
    access_token = create_access_token(token_data)
    return {"token": access_token, "message": "Login successful"}

@app.post("/set_secret", response_model=MessageResponse)
def set_secret(
    payload: SetSecretRequest,
    current_user: sqlite3.Row = Depends(get_current_user),
):
    if current_user["username"] != payload.username:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    store_secret(current_user["id"], payload.secret)
    return {"message": "Secret has been set successfully"}

@app.get("/get_secret", response_model=SecretResponse)
def get_secret(
    username: str,
    current_user: sqlite3.Row = Depends(get_current_user),
):
    if current_user["username"] != username:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    secret = retrieve_secret(current_user["id"])
    # Per OpenAPI spec, always return 200; if no secret, return empty string
    return {"secret": secret or ""}

# ---------- Run server ----------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)