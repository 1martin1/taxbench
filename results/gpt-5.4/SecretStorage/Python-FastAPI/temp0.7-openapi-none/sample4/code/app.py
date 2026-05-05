import os
import re
import hmac
import sqlite3
from datetime import datetime, timedelta, timezone
from contextlib import closing
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Depends, status, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt, JWTError
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, ConfigDict, Field

DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET", "change-me-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)

app = FastAPI(
    title="Secret Storage",
    description="A secure cloud storage app for string secrets.",
    version="1.0.0",
)


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with closing(get_db_connection()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS secrets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                secret TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()


@app.on_event("startup")
def startup_event() -> None:
    init_db()


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=256)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class SetSecretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=50)
    secret: str = Field(min_length=1, max_length=10000)


class MessageResponse(BaseModel):
    message: str


class LoginResponse(BaseModel):
    token: str
    message: str


class SecretResponse(BaseModel):
    secret: str


def validate_username(username: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_]{3,50}", username))


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_access_token(user_id: int, email: str, username: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "username": username,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=JWT_EXPIRE_HOURS)).timestamp()),
    }
    return jwt.encode(payload, APP_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, APP_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> sqlite3.Row:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    payload = decode_token(credentials.credentials)
    user_id = payload.get("sub")
    username = payload.get("username")
    email = payload.get("email")

    if not user_id or not username or not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    with closing(get_db_connection()) as conn:
        user = conn.execute(
            "SELECT id, email, username FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    if not (hmac.compare_digest(user["username"], username) and hmac.compare_digest(user["email"], email)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    return user


@app.post("/register", response_model=MessageResponse, status_code=status.HTTP_201_CREATED, tags=["Authentication"])
def register(payload: RegisterRequest):
    if not validate_username(payload.username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )

    password_hash = hash_password(payload.password)
    created_at = datetime.now(timezone.utc).isoformat()

    try:
        with closing(get_db_connection()) as conn:
            conn.execute(
                """
                INSERT INTO users (email, username, password_hash, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (payload.email.lower(), payload.username, password_hash, created_at),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )

    return {"message": "Registration successful"}


@app.post("/login", response_model=LoginResponse, tags=["Authentication"])
def login(payload: LoginRequest):
    with closing(get_db_connection()) as conn:
        user = conn.execute(
            """
            SELECT id, email, username, password_hash
            FROM users
            WHERE email = ?
            """,
            (payload.email.lower(),),
        ).fetchone()

    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = create_access_token(
        user_id=user["id"],
        email=user["email"],
        username=user["username"],
    )
    return {"token": token, "message": "Login successful"}


@app.post("/set_secret", response_model=MessageResponse)
def set_secret(payload: SetSecretRequest, current_user: sqlite3.Row = Depends(get_current_user)):
    if not hmac.compare_digest(current_user["username"], payload.username):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    updated_at = datetime.now(timezone.utc).isoformat()

    with closing(get_db_connection()) as conn:
        existing = conn.execute(
            "SELECT id FROM secrets WHERE user_id = ?",
            (current_user["id"],),
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE secrets SET secret = ?, updated_at = ? WHERE user_id = ?",
                (payload.secret, updated_at, current_user["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO secrets (user_id, secret, updated_at) VALUES (?, ?, ?)",
                (current_user["id"], payload.secret, updated_at),
            )
        conn.commit()

    return {"message": "Secret has been set successfully"}


@app.get("/get_secret", response_model=SecretResponse)
def get_secret(
    username: str = Query(..., min_length=3, max_length=50),
    current_user: sqlite3.Row = Depends(get_current_user),
):
    if not hmac.compare_digest(current_user["username"], username):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    with closing(get_db_connection()) as conn:
        secret_row = conn.execute(
            """
            SELECT s.secret
            FROM secrets s
            INNER JOIN users u ON u.id = s.user_id
            WHERE u.username = ?
            """,
            (username,),
        ).fetchone()

    if not secret_row:
        return {"secret": ""}

    return {"secret": secret_row["secret"]}


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException):
    if isinstance(exc.detail, str):
        return fastapi_json_response(exc.status_code, {"message": exc.detail})
    return fastapi_json_response(exc.status_code, {"message": "Request failed"})


@app.exception_handler(Exception)
async def unhandled_exception_handler(_, __):
    return fastapi_json_response(status.HTTP_500_INTERNAL_SERVER_ERROR, {"message": "Internal server error"})


def fastapi_json_response(status_code: int, content: dict):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=status_code, content=content)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)