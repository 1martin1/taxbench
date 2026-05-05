import os
import sqlite3
import base64
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Dict, List

from fastapi import (
    FastAPI,
    Depends,
    HTTPException,
    status,
    Request,
    Query,
    Body,
)
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, ValidationError
from jose import JWTError, jwt
from passlib.context import CryptContext

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError("APP_SECRET environment variable not set")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Limits
MAX_CONTENT_LENGTH = 16 * 1024  # 16 KiB for any request
MAX_EMAIL_LENGTH = 254
MAX_USERNAME_LENGTH = 150
MAX_PASSWORD_LENGTH = 128
MAX_SECRET_LENGTH = 1024

# Rate limiting (simple in‑memory)
RATE_LIMIT = 5  # requests
RATE_PERIOD = 60  # seconds
_rate_store: Dict[tuple, List[datetime]] = {}

# ----------------------------------------------------------------------
# FastAPI app
# ----------------------------------------------------------------------
app = FastAPI(title="Secret Storage", version="1.0.0")
bearer_scheme = HTTPBearer()


# ----------------------------------------------------------------------
# Database utilities
# ----------------------------------------------------------------------
DB_PATH = "db.sqlite3"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS secrets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                secret TEXT NOT NULL,
                FOREIGN KEY(username) REFERENCES users(username) ON DELETE CASCADE
            )
            """
        )
        conn.commit()


init_db()


# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, APP_SECRET, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, APP_SECRET, algorithms=[ALGORITHM])
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        ) from e


def encrypt_secret(plain: str) -> str:
    """Simple XOR‑based encryption + base64 (not production‑grade)."""
    key = hashlib.sha256(APP_SECRET.encode()).digest()
    plain_bytes = plain.encode()
    encrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(plain_bytes))
    return base64.urlsafe_b64encode(encrypted).decode()


def decrypt_secret(cipher: str) -> str:
    key = hashlib.sha256(APP_SECRET.encode()).digest()
    encrypted = base64.urlsafe_b64decode(cipher.encode())
    decrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(encrypted))
    return decrypted.decode()


async def get_current_username(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> str:
    payload = decode_token(credentials.credentials)
    username: Optional[str] = payload.get("sub")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )
    return username


def enforce_rate_limit(request: Request):
    ip = request.client.host
    path = request.url.path
    now = datetime.utcnow()
    key = (ip, path)
    timestamps = _rate_store.get(key, [])
    # Keep only timestamps within the period
    timestamps = [ts for ts in timestamps if (now - ts).total_seconds() < RATE_PERIOD]
    if len(timestamps) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many requests")
    timestamps.append(now)
    _rate_store[key] = timestamps


# ----------------------------------------------------------------------
# Pydantic models with length limits
# ----------------------------------------------------------------------
class RegisterRequest(BaseModel):
    email: EmailStr = Field(..., max_length=MAX_EMAIL_LENGTH)
    username: str = Field(..., min_length=1, max_length=MAX_USERNAME_LENGTH)
    password: str = Field(..., min_length=1, max_length=MAX_PASSWORD_LENGTH)


class LoginRequest(BaseModel):
    email: EmailStr = Field(..., max_length=MAX_EMAIL_LENGTH)
    password: str = Field(..., min_length=1, max_length=MAX_PASSWORD_LENGTH)


class SetSecretRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=MAX_USERNAME_LENGTH)
    secret: str = Field(..., min_length=1, max_length=MAX_SECRET_LENGTH)


# ----------------------------------------------------------------------
# Middleware for content‑length enforcement
# ----------------------------------------------------------------------
@app.middleware("http")
async def enforce_content_length(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_CONTENT_LENGTH:
        return JSONResponse(
            status_code=413,
            content={"message": "Request body too large"},
        )
    response = await call_next(request)
    return response


# ----------------------------------------------------------------------
# Exception handlers
# ----------------------------------------------------------------------
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"message": exc.detail},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Convert FastAPI's 422 into 400 as per specification
    return JSONResponse(
        status_code=400,
        content={"message": "Invalid request data"},
    )


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------
@app.post("/register", status_code=201)
def register(
    request: RegisterRequest,
    db: sqlite3.Connection = Depends(get_db),
    req: Request = Depends(),
):
    enforce_rate_limit(req)
    cur = db.cursor()
    cur.execute(
        "SELECT id FROM users WHERE email = ? OR username = ?",
        (request.email, request.username),
    )
    if cur.fetchone():
        raise HTTPException(
            status_code=400,
            detail="Email or username already in use",
        )
    password_hash = get_password_hash(request.password)
    cur.execute(
        "INSERT INTO users (email, username, password_hash) VALUES (?, ?, ?)",
        (request.email, request.username, password_hash),
    )
    db.commit()
    return {"message": "Registration successful"}


@app.post("/login")
def login(
    request: LoginRequest,
    db: sqlite3.Connection = Depends(get_db),
    req: Request = Depends(),
):
    enforce_rate_limit(req)
    cur = db.cursor()
    cur.execute("SELECT username, password_hash FROM users WHERE email = ?", (request.email,))
    row = cur.fetchone()
    if not row or not verify_password(request.password, row["password_hash"]):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password",
        )
    access_token = create_access_token(data={"sub": row["username"]})
    return {"token": access_token, "message": "Login successful"}


@app.post("/set_secret")
def set_secret(
    payload: SetSecretRequest,
    current_user: str = Depends(get_current_username),
    db: sqlite3.Connection = Depends(get_db),
    req: Request = Depends(),
):
    enforce_rate_limit(req)
    if payload.username != current_user:
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication token",
        )
    encrypted = encrypt_secret(payload.secret)
    cur = db.cursor()
    # Manual upsert compatible with older SQLite versions
    try:
        cur.execute(
            "INSERT INTO secrets (username, secret) VALUES (?, ?)",
            (payload.username, encrypted),
        )
    except sqlite3.IntegrityError:
        cur.execute(
            "UPDATE secrets SET secret = ? WHERE username = ?",
            (encrypted, payload.username),
        )
    db.commit()
    return {"message": "Secret has been set successfully"}


@app.get("/get_secret")
def get_secret(
    username: str = Query(..., min_length=1, max_length=MAX_USERNAME_LENGTH),
    current_user: str = Depends(get_current_username),
    db: sqlite3.Connection = Depends(get_db),
    req: Request = Depends(),
):
    enforce_rate_limit(req)
    if username != current_user:
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication token",
        )
    cur = db.cursor()
    cur.execute("SELECT secret FROM secrets WHERE username = ?", (username,))
    row = cur.fetchone()
    secret = ""
    if row:
        try:
            secret = decrypt_secret(row["secret"])
        except Exception:
            # In case decryption fails, treat as empty
            secret = ""
    return {"secret": secret}


# ----------------------------------------------------------------------
# Run server
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)