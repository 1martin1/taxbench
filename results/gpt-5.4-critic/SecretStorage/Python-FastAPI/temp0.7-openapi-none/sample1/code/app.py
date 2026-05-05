import os
import sqlite3
import hashlib
import hmac
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from typing import Optional

import jwt
import uvicorn
from fastapi import FastAPI, HTTPException, Depends, status, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, ConfigDict, field_validator


DB_PATH = "db.sqlite3"
APP_SECRET = os.environ.get("APP_SECRET")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24
PBKDF2_ITERATIONS = 200_000
DB_TIMEOUT_SECONDS = 5.0
AUTH_RATE_LIMIT_WINDOW_SECONDS = 60
AUTH_RATE_LIMIT_MAX_REQUESTS = 10

security = HTTPBearer(auto_error=False)

_auth_rate_limit_lock = threading.Lock()
_auth_rate_limit_state: dict[str, list[float]] = {}


def get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        first_ip = forwarded_for.split(",")[0].strip()
        if first_ip:
            return first_ip
    client = request.client
    return client.host if client and client.host else "unknown"


def check_auth_rate_limit(key: str) -> None:
    now = time.time()
    cutoff = now - AUTH_RATE_LIMIT_WINDOW_SECONDS

    with _auth_rate_limit_lock:
        timestamps = _auth_rate_limit_state.get(key, [])
        timestamps = [ts for ts in timestamps if ts >= cutoff]

        if len(timestamps) >= AUTH_RATE_LIMIT_MAX_REQUESTS:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token",
            )

        timestamps.append(now)
        _auth_rate_limit_state[key] = timestamps

        if len(_auth_rate_limit_state) > 10000:
            stale_keys = [
                state_key
                for state_key, values in _auth_rate_limit_state.items()
                if not values or all(ts < cutoff for ts in values)
            ]
            for stale_key in stale_keys:
                _auth_rate_limit_state.pop(stale_key, None)


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db() -> None:
    conn = get_db_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
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
    finally:
        conn.close()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    )
    return digest.hex(), salt


def verify_password(password: str, password_hash: str, password_salt: str) -> bool:
    computed_hash, _ = hash_password(password, password_salt)
    return hmac.compare_digest(computed_hash, password_hash)


def create_access_token(user_id: int, email: str, username: str) -> str:
    if not APP_SECRET:
        raise RuntimeError("APP_SECRET environment variable must be set")

    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "username": username,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=JWT_EXPIRE_HOURS)).timestamp()),
    }
    return jwt.encode(payload, APP_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    if not APP_SECRET:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> sqlite3.Row:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    payload = decode_access_token(credentials.credentials)
    user_id = payload.get("sub")
    username = payload.get("username")

    if not user_id or not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    conn = get_db_connection()
    try:
        user = conn.execute(
            "SELECT id, email, username FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    finally:
        conn.close()

    if user is None or user["username"] != username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    return user


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=8, max_length=1024)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        email = value.strip().lower()
        if not email or "@" not in email:
            raise ValueError("invalid email")
        local, _, domain = email.partition("@")
        if not local or not domain or "." not in domain or domain.startswith(".") or domain.endswith("."):
            raise ValueError("invalid email")
        return email


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        email = value.strip().lower()
        if not email or "@" not in email:
            raise ValueError("invalid email")
        local, _, domain = email.partition("@")
        if not local or not domain or "." not in domain or domain.startswith(".") or domain.endswith("."):
            raise ValueError("invalid email")
        return email


class SetSecretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=150)
    secret: str = Field(min_length=1, max_length=10000)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not APP_SECRET:
        raise RuntimeError("APP_SECRET environment variable must be set")
    init_db()
    yield


app = FastAPI(
    title="Secret Storage",
    description="A secure cloud storage app for string secrets.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException):
    if isinstance(exc.detail, str):
        return JSONResponse(
            status_code=exc.status_code,
            content={"message": exc.detail},
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"message": "Request failed"},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    if request.url.path == "/register":
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Email already in use or invalid data"},
        )
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"message": "Request failed"},
    )


@app.post("/register", status_code=201, tags=["Authentication"])
def register(payload: RegisterRequest, request: Request):
    check_auth_rate_limit(f"register:{get_client_ip(request)}")

    username = payload.username.strip()
    if not username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )

    password_hash, password_salt = hash_password(payload.password)

    conn = get_db_connection()
    try:
        existing = conn.execute(
            "SELECT 1 FROM users WHERE email = ? OR username = ?",
            (payload.email, username),
        ).fetchone()
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already in use or invalid data",
            )

        conn.execute(
            """
            INSERT INTO users (email, username, password_hash, password_salt, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                payload.email,
                username,
                password_hash,
                password_salt,
                utc_now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {"message": "Registration successful"}


@app.post("/login", tags=["Authentication"])
def login(payload: LoginRequest, request: Request):
    check_auth_rate_limit(f"login:{get_client_ip(request)}:{payload.email}")

    conn = get_db_connection()
    try:
        user = conn.execute(
            """
            SELECT id, email, username, password_hash, password_salt
            FROM users
            WHERE email = ?
            """,
            (payload.email,),
        ).fetchone()
    finally:
        conn.close()

    if user is None or not verify_password(
        payload.password, user["password_hash"], user["password_salt"]
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = create_access_token(user["id"], user["email"], user["username"])
    return {"token": token, "message": "Login successful"}


@app.post("/set_secret")
def set_secret(
    payload: SetSecretRequest,
    current_user: sqlite3.Row = Depends(get_current_user),
):
    if payload.username != current_user["username"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    conn = get_db_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM secrets WHERE user_id = ?",
            (current_user["id"],),
        ).fetchone()

        if existing is None:
            conn.execute(
                """
                INSERT INTO secrets (user_id, secret, updated_at)
                VALUES (?, ?, ?)
                """,
                (current_user["id"], payload.secret, utc_now_iso()),
            )
        else:
            conn.execute(
                """
                UPDATE secrets
                SET secret = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (payload.secret, utc_now_iso(), current_user["id"]),
            )
        conn.commit()
    finally:
        conn.close()

    return {"message": "Secret has been set successfully"}


@app.get("/get_secret")
def get_secret(username: str, current_user: sqlite3.Row = Depends(get_current_user)):
    if username != current_user["username"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    conn = get_db_connection()
    try:
        user = conn.execute(
            "SELECT id FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token",
            )

        secret_row = conn.execute(
            "SELECT secret FROM secrets WHERE user_id = ?",
            (user["id"],),
        ).fetchone()
    finally:
        conn.close()

    if secret_row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    return {"secret": secret_row["secret"]}


if __name__ == "__main__":
    if not APP_SECRET:
        raise RuntimeError("APP_SECRET environment variable must be set")
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)