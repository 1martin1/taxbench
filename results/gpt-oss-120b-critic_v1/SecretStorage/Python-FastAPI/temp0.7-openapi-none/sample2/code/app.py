import os
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, List

import jwt
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Security,
    status,
)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field

# -------------------- Configuration --------------------
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError("APP_SECRET environment variable must be set")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

DATABASE = "db.sqlite3"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)

# Rate limiting config
RATE_LIMIT = 5  # requests
RATE_PERIOD = 60  # seconds
_rate_limit_store: Dict[str, List[float]] = {}
# -------------------- Database Utilities --------------------
def get_db():
    conn = sqlite3.connect(DATABASE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS secrets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            secret TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        """
    )
    conn.commit()
    conn.close()

# -------------------- Pydantic Schemas --------------------
class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=1, max_length=30)
    password: str = Field(..., min_length=8, max_length=128)

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)

class SetSecretRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=30)
    secret: str = Field(..., min_length=1, max_length=1024)

class TokenResponse(BaseModel):
    token: str
    message: str = "Login successful"

class MessageResponse(BaseModel):
    message: str

class GetSecretResponse(BaseModel):
    secret: str

# -------------------- Helper Functions --------------------
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, APP_SECRET, algorithm=ALGORITHM)

def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired"
        )
    except jwt.JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token"
        )

def get_user_by_email(email: str) -> Optional[sqlite3.Row]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
    user = cursor.fetchone()
    conn.close()
    return user

def get_user_by_username(username: str) -> Optional[sqlite3.Row]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
    user = cursor.fetchone()
    conn.close()
    return user

def get_user_by_id(user_id: int) -> Optional[sqlite3.Row]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

def rate_limit(request: Request, endpoint: str):
    client_ip = request.client.host
    key = f"{client_ip}:{endpoint}"
    now = time.time()
    timestamps = _rate_limit_store.get(key, [])
    # Remove timestamps older than period
    timestamps = [ts for ts in timestamps if now - ts < RATE_PERIOD]
    if len(timestamps) >= RATE_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests, please try again later",
        )
    timestamps.append(now)
    _rate_limit_store[key] = timestamps

def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
):
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    token = credentials.credentials
    payload = decode_access_token(token)
    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )
    user = get_user_by_id(int(user_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user

# -------------------- Middleware --------------------
MAX_REQUEST_SIZE = 2 * 1024  # 2 KB

async def request_size_limit_middleware(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length is not None and int(content_length) > MAX_REQUEST_SIZE:
        return JSONResponse(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            content={"detail": "Request payload too large"},
        )
    return await call_next(request)

# -------------------- FastAPI App --------------------
app = FastAPI(
    title="Secret Storage",
    version="1.0.0",
    description="A secure cloud storage app for string secrets.",
)

app.middleware("http")(request_size_limit_middleware)

@app.on_event("startup")
def on_startup():
    init_db()

# -------------------- Endpoints --------------------
@app.post(
    "/register",
    response_model=MessageResponse,
    status_code=201,
    tags=["Authentication"],
)
def register(request: RegisterRequest, http_request: Request):
    rate_limit(http_request, "register")
    if get_user_by_email(request.email):
        raise HTTPException(status_code=400, detail="Email already in use")
    if get_user_by_username(request.username):
        raise HTTPException(status_code=400, detail="Username already in use")
    password_hash = get_password_hash(request.password)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO users (email, username, password_hash) VALUES (?, ?, ?)",
        (request.email, request.username, password_hash),
    )
    conn.commit()
    conn.close()
    return {"message": "Registration successful"}

@app.post(
    "/login",
    response_model=TokenResponse,
    tags=["Authentication"],
)
def login(request: LoginRequest, http_request: Request):
    rate_limit(http_request, "login")
    user = get_user_by_email(request.email)
    if not user or not verify_password(request.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token_data = {"sub": str(user["id"]), "username": user["username"]}
    token = create_access_token(token_data)
    return {"token": token, "message": "Login successful"}

@app.post(
    "/set_secret",
    response_model=MessageResponse,
    tags=["Authentication"],
    dependencies=[Depends(get_current_user)],
)
def set_secret(payload: SetSecretRequest, current_user=Depends(get_current_user)):
    # Ensure the token belongs to the username provided
    if current_user["username"] != payload.username:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM secrets WHERE user_id = ?", (current_user["id"],))
    existing = cursor.fetchone()
    if existing:
        cursor.execute(
            "UPDATE secrets SET secret = ? WHERE user_id = ?",
            (payload.secret, current_user["id"]),
        )
    else:
        cursor.execute(
            "INSERT INTO secrets (user_id, secret) VALUES (?, ?)",
            (current_user["id"], payload.secret),
        )
    conn.commit()
    conn.close()
    return {"message": "Secret has been set successfully"}

@app.get(
    "/get_secret",
    response_model=GetSecretResponse,
    tags=["Authentication"],
    dependencies=[Depends(get_current_user)],
)
def get_secret(username: str, current_user=Depends(get_current_user)):
    # Users can only fetch their own secret
    if current_user["username"] != username:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT s.secret FROM secrets s
        JOIN users u ON s.user_id = u.id
        WHERE u.username = ?
        """,
        (username,),
    )
    row = cursor.fetchone()
    conn.close()
    secret_value = row["secret"] if row else ""
    return {"secret": secret_value}

# -------------------- Run Server --------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)