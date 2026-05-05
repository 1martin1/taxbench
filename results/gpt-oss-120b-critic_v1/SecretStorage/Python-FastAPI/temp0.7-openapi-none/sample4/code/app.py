import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from typing import Generator

import jwt
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field

# ---------- Configuration ----------
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    # Fallback to a randomly generated secret for development/testing.
    # In production, the environment variable should be set.
    APP_SECRET = secrets.token_urlsafe(32)

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 1 day

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)

DB_PATH = "db.sqlite3"

# ---------- Limits ----------
MAX_EMAIL_LENGTH = 254
MAX_USERNAME_LENGTH = 150
MAX_PASSWORD_LENGTH = 128
MAX_SECRET_LENGTH = 2000

# ---------- Database Utilities ----------
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS secrets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                secret TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            """
        )
        conn.commit()


# ---------- Pydantic Models ----------
class RegisterRequest(BaseModel):
    email: EmailStr = Field(..., max_length=MAX_EMAIL_LENGTH)
    username: str = Field(..., min_length=1, max_length=MAX_USERNAME_LENGTH)
    password: str = Field(..., min_length=1, max_length=MAX_PASSWORD_LENGTH)


class RegisterResponse(BaseModel):
    message: str


class LoginRequest(BaseModel):
    email: EmailStr = Field(..., max_length=MAX_EMAIL_LENGTH)
    password: str = Field(..., min_length=1, max_length=MAX_PASSWORD_LENGTH)


class LoginResponse(BaseModel):
    token: str
    message: str


class SetSecretRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=MAX_USERNAME_LENGTH)
    secret: str = Field(..., min_length=1, max_length=MAX_SECRET_LENGTH)


class SetSecretResponse(BaseModel):
    message: str


class GetSecretResponse(BaseModel):
    secret: str


class ErrorResponse(BaseModel):
    message: str


# ---------- Helper Functions ----------
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, APP_SECRET, algorithm=JWT_ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token")


def get_current_username(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )
    payload = decode_access_token(credentials.credentials)
    username = payload.get("sub")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )
    return username


def get_user_by_email(conn: sqlite3.Connection, email: str):
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE email = ?", (email,))
    return cur.fetchone()


def get_user_by_username(conn: sqlite3.Connection, username: str):
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = ?", (username,))
    return cur.fetchone()


def insert_user(conn: sqlite3.Connection, email: str, username: str, password_hash: str) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (email, username, password_hash) VALUES (?, ?, ?)",
        (email, username, password_hash),
    )
    conn.commit()
    return cur.lastrowid


def set_user_secret(conn: sqlite3.Connection, user_id: int, secret: str) -> None:
    cur = conn.cursor()
    cur.execute("DELETE FROM secrets WHERE user_id = ?", (user_id,))
    cur.execute(
        "INSERT INTO secrets (user_id, secret) VALUES (?, ?)",
        (user_id, secret),
    )
    conn.commit()


def get_user_secret(conn: sqlite3.Connection, user_id: int) -> str | None:
    cur = conn.cursor()
    cur.execute("SELECT secret FROM secrets WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    return row["secret"] if row else None


# ---------- FastAPI Application ----------
app = FastAPI(
    title="Secret Storage",
    description="A secure cloud storage app for string secrets.",
    version="1.0.0",
)


@app.on_event("startup")
def startup_event() -> None:
    init_db()


# ---------- Endpoints ----------
@app.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    responses={400: {"model": ErrorResponse}},
    summary="User registration",
    tags=["Authentication"],
)
def register(payload: RegisterRequest, db: sqlite3.Connection = Depends(get_db)):
    # Validate email/username uniqueness
    if get_user_by_email(db, payload.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use",
        )
    if get_user_by_username(db, payload.username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already taken",
        )
    password_hash = get_password_hash(payload.password)
    insert_user(db, payload.email, payload.username, password_hash)
    return RegisterResponse(message="Registration successful")


@app.post(
    "/login",
    response_model=LoginResponse,
    responses={401: {"model": ErrorResponse}},
    summary="User login",
    tags=["Authentication"],
)
def login(payload: LoginRequest, db: sqlite3.Connection = Depends(get_db)):
    user = get_user_by_email(db, payload.email)
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    token = create_access_token({"sub": user["username"]})
    return LoginResponse(token=token, message="Login successful")


@app.post(
    "/set_secret",
    response_model=SetSecretResponse,
    responses={401: {"model": ErrorResponse}, 400: {"model": ErrorResponse}},
    summary="Set secret",
    tags=["Authentication"],
)
def set_secret(
    payload: SetSecretRequest,
    current_username: str = Depends(get_current_username),
    db: sqlite3.Connection = Depends(get_db),
):
    # Ensure the token belongs to the username provided
    if payload.username != current_username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )
    # Length validation already enforced by Pydantic, but double‑check for safety
    if len(payload.secret) > MAX_SECRET_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Secret exceeds maximum length of {MAX_SECRET_LENGTH}",
        )
    user = get_user_by_username(db, payload.username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    set_user_secret(db, user["id"], payload.secret)
    return SetSecretResponse(message="Secret has been set successfully")


@app.get(
    "/get_secret",
    response_model=GetSecretResponse,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    summary="Get secret",
    tags=["Authentication"],
)
def get_secret(
    username: str = Query(..., max_length=MAX_USERNAME_LENGTH),
    current_username: str = Depends(get_current_username),
    db: sqlite3.Connection = Depends(get_db),
):
    # Token must belong to the requested username
    if username != current_username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )
    user = get_user_by_username(db, username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    secret = get_user_secret(db, user["id"])
    if secret is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Secret not found",
        )
    return GetSecretResponse(secret=secret)


# ---------- Run Server ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)