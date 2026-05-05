import os
import re
import hmac
import time
import sqlite3
import threading
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, ConfigDict, Field, field_validator


DB_PATH = "db.sqlite3"
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

# Security/resource controls
MAX_SECRET_LENGTH = 10000
MAX_DB_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MiB
DB_POOL_SIZE = 8
SQLITE_TIMEOUT_SECONDS = 5.0

RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMITS = {
    "/register": 10,
    "/login": 10,
    "/set_secret": 30,
    "/get_secret": 60,
}

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def get_app_secret() -> str:
    secret = os.getenv("APP_SECRET")
    if not secret or len(secret) < 32:
        raise RuntimeError("APP_SECRET must be set and at least 32 characters long")
    return secret


APP_SECRET = get_app_secret()

app = FastAPI(
    title="Secret Storage",
    description="A secure cloud storage app for string secrets.",
    version="1.0.0",
)


class SQLitePool:
    def __init__(self, db_path: str, size: int) -> None:
        self.db_path = db_path
        self.size = size
        self._queue = deque()
        self._lock = threading.Lock()
        self._semaphore = threading.BoundedSemaphore(size)
        self._initialized = False

    def initialize(self) -> None:
        with self._lock:
            if self._initialized:
                return
            for _ in range(self.size):
                conn = sqlite3.connect(
                    self.db_path,
                    timeout=SQLITE_TIMEOUT_SECONDS,
                    isolation_level=None,
                    check_same_thread=True,
                )
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("PRAGMA busy_timeout = 5000")
                self._queue.append(conn)
            self._initialized = True

    @contextmanager
    def connection(self):
        acquired = self._semaphore.acquire(timeout=SQLITE_TIMEOUT_SECONDS)
        if not acquired:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Service temporarily unavailable",
            )

        conn = None
        try:
            with self._lock:
                conn = self._queue.popleft()
            yield conn
        finally:
            if conn is not None:
                with self._lock:
                    self._queue.append(conn)
            self._semaphore.release()

    def close_all(self) -> None:
        with self._lock:
            while self._queue:
                conn = self._queue.popleft()
                conn.close()
            self._initialized = False


db_pool = SQLitePool(DB_PATH, DB_POOL_SIZE)


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._events = {}
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: int) -> None:
        now = time.time()
        with self._lock:
            bucket = self._events.get(key)
            if bucket is None:
                bucket = deque()
                self._events[key] = bucket

            cutoff = now - window_seconds
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()

            if len(bucket) >= limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many requests",
                )

            bucket.append(now)


rate_limiter = InMemoryRateLimiter()


def get_client_identifier(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()
        if client_ip:
            return client_ip
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def enforce_rate_limit(request: Request) -> None:
    path = request.url.path
    limit = RATE_LIMITS.get(path)
    if limit is None:
        return
    client_id = get_client_identifier(request)
    rate_limiter.check(f"{path}:{client_id}", limit, RATE_LIMIT_WINDOW_SECONDS)


def validate_username(username: str) -> bool:
    return bool(USERNAME_PATTERN.fullmatch(username))


def validate_email(email: str) -> bool:
    if len(email) > 320:
        return False
    return bool(EMAIL_PATTERN.fullmatch(email))


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_access_token(subject: str, username: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "username": username,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)).timestamp()),
    }
    return jwt.encode(payload, APP_SECRET, algorithm=JWT_ALGORITHM)


def extract_bearer_token(authorization: Optional[str]) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )
    parts = authorization.strip().split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )
    return parts[1].strip()


def ensure_storage_capacity_for_secret(secret: str) -> None:
    try:
        current_size = os.path.getsize(DB_PATH)
    except FileNotFoundError:
        current_size = 0

    projected_growth = len(secret.encode("utf-8")) + 4096
    if current_size + projected_growth > MAX_DB_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service temporarily unavailable",
        )


def init_db() -> None:
    db_pool.initialize()
    with db_pool.connection() as conn:
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


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.on_event("shutdown")
def shutdown_event() -> None:
    db_pool.close_all()


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)

    @field_validator("email")
    @classmethod
    def validate_email_field(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not validate_email(normalized):
            raise ValueError("invalid email")
        return normalized


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("email")
    @classmethod
    def validate_email_field(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not validate_email(normalized):
            raise ValueError("invalid email")
        return normalized


class SetSecretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=64)
    secret: str = Field(min_length=1, max_length=MAX_SECRET_LENGTH)


class MessageResponse(BaseModel):
    message: str


class LoginResponse(BaseModel):
    token: str
    message: str


class SecretResponse(BaseModel):
    secret: str


def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    enforce_rate_limit(request)
    token = extract_bearer_token(authorization)
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=[JWT_ALGORITHM])
        email = payload.get("sub")
        username = payload.get("username")
        if not isinstance(email, str) or not isinstance(username, str):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token",
            )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    with db_pool.connection() as conn:
        row = conn.execute(
            "SELECT id, email, username FROM users WHERE email = ?",
            (email,),
        ).fetchone()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token",
            )
        if not hmac.compare_digest(row["username"], username):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token",
            )
        return row


@app.post(
    "/register",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Authentication"],
    security=[],
)
def register(request: RegisterRequest, http_request: Request):
    enforce_rate_limit(http_request)

    if not validate_username(request.username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )

    with db_pool.connection() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE email = ? OR username = ?",
            (request.email, request.username),
        ).fetchone()
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already in use or invalid data",
            )

        conn.execute(
            """
            INSERT INTO users (email, username, password_hash, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                request.email,
                request.username,
                hash_password(request.password),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        return {"message": "Registration successful"}


@app.post(
    "/login",
    response_model=LoginResponse,
    tags=["Authentication"],
    security=[],
)
def login(request: LoginRequest, http_request: Request):
    enforce_rate_limit(http_request)

    with db_pool.connection() as conn:
        row = conn.execute(
            "SELECT email, username, password_hash FROM users WHERE email = ?",
            (request.email,),
        ).fetchone()
        if row is None or not verify_password(request.password, row["password_hash"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

        token = create_access_token(row["email"], row["username"])
        return {"token": token, "message": "Login successful"}


@app.post(
    "/set_secret",
    response_model=MessageResponse,
    openapi_extra={"security": [{"bearerAuth": []}]},
)
def set_secret(request: SetSecretRequest, current_user=Depends(get_current_user)):
    if not validate_username(request.username):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    if not hmac.compare_digest(current_user["username"], request.username):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    ensure_storage_capacity_for_secret(request.secret)

    with db_pool.connection() as conn:
        user_row = conn.execute(
            "SELECT id FROM users WHERE username = ?",
            (request.username,),
        ).fetchone()
        if user_row is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token",
            )

        existing_secret = conn.execute(
            "SELECT id FROM secrets WHERE user_id = ?",
            (user_row["id"],),
        ).fetchone()

        now = datetime.now(timezone.utc).isoformat()
        if existing_secret is None:
            conn.execute(
                """
                INSERT INTO secrets (user_id, secret, updated_at)
                VALUES (?, ?, ?)
                """,
                (user_row["id"], request.secret, now),
            )
        else:
            conn.execute(
                """
                UPDATE secrets
                SET secret = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (request.secret, now, user_row["id"]),
            )
        return {"message": "Secret has been set successfully"}


@app.get(
    "/get_secret",
    response_model=SecretResponse,
    openapi_extra={"security": [{"bearerAuth": []}]},
)
def get_secret(username: str, current_user=Depends(get_current_user)):
    if not validate_username(username):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    if not hmac.compare_digest(current_user["username"], username):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    with db_pool.connection() as conn:
        row = conn.execute(
            """
            SELECT s.secret
            FROM secrets s
            INNER JOIN users u ON u.id = s.user_id
            WHERE u.username = ?
            """,
            (username,),
        ).fetchone()

        if row is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token",
            )

        return {"secret": row["secret"]}


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return JSONResponse(status_code=exc.status_code, content={"message": detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(_, __):
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"message": "Internal server error"},
    )


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)