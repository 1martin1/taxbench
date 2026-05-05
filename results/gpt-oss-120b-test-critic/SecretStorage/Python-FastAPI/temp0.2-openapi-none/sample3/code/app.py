import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, status, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field
from passlib.context import CryptContext
import jwt

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError("APP_SECRET environment variable must be set")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 1 day

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ----------------------------------------------------------------------
# FastAPI app
# ----------------------------------------------------------------------
app = FastAPI(
    title="Secret Storage",
    description="A secure cloud storage app for string secrets.",
    version="1.0.0",
)

bearer_scheme = HTTPBearer(auto_error=False)

# ----------------------------------------------------------------------
# Database utilities
# ----------------------------------------------------------------------
DB_PATH = "db.sqlite3"


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they do not exist."""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS secrets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE NOT NULL,
                secret TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()


@app.on_event("startup")
def on_startup():
    init_db()


# ----------------------------------------------------------------------
# Pydantic models
# ----------------------------------------------------------------------
class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=1, max_length=150)
    password: str = Field(..., min_length=1, max_length=128)


class RegisterResponse(BaseModel):
    message: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)


class LoginResponse(BaseModel):
    token: str
    message: str


class SetSecretRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=150)
    secret: str = Field(..., max_length=1024)


class SetSecretResponse(BaseModel):
    message: str


class GetSecretResponse(BaseModel):
    secret: str


class MessageResponse(BaseModel):
    message: str


# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------
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


def create_user(email: str, username: str, password: str) -> sqlite3.Row:
    password_hash = pwd_context.hash(password)
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO users (email, username, password_hash) VALUES (?, ?, ?)",
                (email, username, password_hash),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=400,
            detail={"message": "Email or username already in use"},
        )
    return get_user_by_username(username)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
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
        raise HTTPException(
            status_code=401,
            detail={"message": "Token has expired"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=401,
            detail={"message": "Invalid authentication token"},
        )


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> sqlite3.Row:
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail={"message": "Not authenticated"},
        )
    token = credentials.credentials
    payload = decode_access_token(token)
    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=401,
            detail={"message": "Invalid token payload"},
        )
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        user = cur.fetchone()
    if user is None:
        raise HTTPException(
            status_code=401,
            detail={"message": "User not found"},
        )
    return user


def set_user_secret(user_id: int, secret: str):
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM secrets WHERE user_id = ?", (user_id,))
        existing = cur.fetchone()
        if existing:
            cur.execute("UPDATE secrets SET secret = ? WHERE user_id = ?", (secret, user_id))
        else:
            cur.execute("INSERT INTO secrets (user_id, secret) VALUES (?, ?)", (user_id, secret))
        conn.commit()


def get_user_secret(user_id: int) -> str:
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT secret FROM secrets WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
    return row["secret"] if row else ""


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------
@app.post(
    "/register",
    response_model=RegisterResponse,
    status_code=201,
    responses={400: {"model": MessageResponse}},
)
def register(payload: RegisterRequest):
    if get_user_by_email(payload.email) or get_user_by_username(payload.username):
        raise HTTPException(
            status_code=400,
            detail={"message": "Email already in use or invalid data"},
        )
    create_user(payload.email, payload.username, payload.password)
    return {"message": "Registration successful"}


@app.post(
    "/login",
    response_model=LoginResponse,
    responses={401: {"model": MessageResponse}},
)
def login(payload: LoginRequest):
    user = get_user_by_email(payload.email)
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(
            status_code=401,
            detail={"message": "Invalid email or password"},
        )
    token_data = {"sub": user["id"], "username": user["username"]}
    token = create_access_token(token_data)
    return {"token": token, "message": "Login successful"}


@app.post(
    "/set_secret",
    response_model=SetSecretResponse,
    responses={401: {"model": MessageResponse}},
)
def set_secret(
    payload: SetSecretRequest,
    current_user: sqlite3.Row = Depends(get_current_user),
):
    if current_user["username"] != payload.username:
        raise HTTPException(
            status_code=401,
            detail={"message": "Invalid authentication token"},
        )
    set_user_secret(current_user["id"], payload.secret)
    return {"message": "Secret has been set successfully"}


@app.get(
    "/get_secret",
    response_model=GetSecretResponse,
    responses={401: {"model": MessageResponse}},
)
def get_secret(
    username: str = Query(..., description="Username to retrieve secret for"),
    current_user: sqlite3.Row = Depends(get_current_user),
):
    if current_user["username"] != username:
        raise HTTPException(
            status_code=401,
            detail={"message": "Invalid authentication token"},
        )
    secret = get_user_secret(current_user["id"])
    return {"secret": secret}


# ----------------------------------------------------------------------
# Run the application
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000, reload=False)