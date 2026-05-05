import json
import os
import re
import sqlite3
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, Optional

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, ConfigDict, Field, field_validator

DB_PATH = "db.sqlite3"
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24

MAX_BODY_BYTES = 16 * 1024
MAX_EMAIL_LENGTH = 254
MAX_USERNAME_LENGTH = 150
MAX_PASSWORD_LENGTH = 1024
MAX_SECRET_LENGTH = 8192

AUTH_WINDOW_SECONDS = 60
AUTH_MAX_REQUESTS = 10
API_WINDOW_SECONDS = 60
API_MAX_REQUESTS = 60

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

app_secret = os.getenv("APP_SECRET")
if not app_secret or not app_secret.strip():
    raise RuntimeError("APP_SECRET environment variable must be set and non-empty")
APP_SECRET = app_secret

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
db_lock = threading.Lock()
rate_limit_lock = threading.Lock()
rate_limit_buckets: Dict[str, Deque[float]] = defaultdict(deque)

bearer_scheme = HTTPBearer(auto_error=False)


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db_lock:
        conn = get_db_connection()
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
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
        finally:
            conn.close()


def create_access_token(subject: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=JWT_EXPIRE_HOURS)).timestamp()),
    }
    return jwt.encode(payload, APP_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> str:
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=[JWT_ALGORITHM])
        subject = payload.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"message": "Invalid authentication token"},
            )
        return subject
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"message": "Invalid authentication token"},
        )


def normalize_email(value: str) -> str:
    email = value.strip().lower()
    if not email or len(email) > MAX_EMAIL_LENGTH or not EMAIL_REGEX.fullmatch(email):
        raise ValueError("invalid email")
    return email


def normalize_username(value: str) -> str:
    username = value.strip()
    if not username or len(username) > MAX_USERNAME_LENGTH:
        raise ValueError("invalid username")
    return username


def get_client_ip(request: Request) -> str:
    client = request.client
    if client and client.host:
        return client.host
    return "unknown"


def enforce_rate_limit(bucket_key: str, limit: int, window_seconds: int) -> None:
    now = time.monotonic()
    with rate_limit_lock:
        bucket = rate_limit_buckets[bucket_key]
        cutoff = now - window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"message": "Too many requests"},
            )
        bucket.append(now)


def require_authentication(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"message": "Invalid authentication token"},
        )
    return decode_access_token(credentials.credentials.strip())


class MessageResponse(BaseModel):
    message: str


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=MAX_EMAIL_LENGTH)
    username: str = Field(min_length=1, max_length=MAX_USERNAME_LENGTH)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        return normalize_username(value)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=MAX_EMAIL_LENGTH)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class LoginResponse(BaseModel):
    token: str
    message: str


class SetSecretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=MAX_USERNAME_LENGTH)
    secret: str = Field(min_length=1, max_length=MAX_SECRET_LENGTH)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        return normalize_username(value)


class SecretResponse(BaseModel):
    secret: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Secret Storage",
    description="A secure cloud storage app for string secrets.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def body_and_rate_limit_middleware(request: Request, call_next):
    try:
        client_ip = get_client_ip(request)
        path = request.url.path

        if request.method in {"POST", "PUT", "PATCH"}:
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > MAX_BODY_BYTES:
                        return JSONResponse(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            content={"message": "Invalid request body"},
                        )
                except ValueError:
                    return JSONResponse(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        content={"message": "Invalid request body"},
                    )

            body = await request.body()
            if len(body) > MAX_BODY_BYTES:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"message": "Invalid request body"},
                )

            if body and request.headers.get("content-type", "").lower().startswith("application/json"):
                try:
                    json.loads(body)
                except json.JSONDecodeError:
                    return JSONResponse(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        content={"message": "Invalid request body"},
                    )

        if path in {"/register", "/login"}:
            enforce_rate_limit(f"auth:{client_ip}:{path}", AUTH_MAX_REQUESTS, AUTH_WINDOW_SECONDS)
        elif path in {"/set_secret", "/get_secret"}:
            enforce_rate_limit(f"api:{client_ip}:{path}", API_MAX_REQUESTS, API_WINDOW_SECONDS)

        response = await call_next(request)
        return response
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    except Exception:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Internal server error"},
        )


@app.post(
    "/register",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Authentication"],
    security=[],
)
def register(payload: RegisterRequest, request: Request):
    email = payload.email
    username = payload.username

    password_hash = pwd_context.hash(payload.password)
    created_at = datetime.now(timezone.utc).isoformat()

    with db_lock:
        conn = get_db_connection()
        try:
            conn.execute(
                """
                INSERT INTO users (email, username, password_hash, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (email, username, password_hash, created_at),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"message": "Email already in use or invalid data"},
            )
        finally:
            conn.close()

    return {"message": "Registration successful"}


@app.post("/login", response_model=LoginResponse, tags=["Authentication"], security=[])
def login(payload: LoginRequest, request: Request):
    email = payload.email

    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT username, password_hash FROM users WHERE email = ?",
            (email,),
        ).fetchone()
    finally:
        conn.close()

    if row is None or not pwd_context.verify(payload.password, row["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"message": "Invalid email or password"},
        )

    token = create_access_token(row["username"])
    return {"token": token, "message": "Login successful"}


@app.post("/set_secret", response_model=MessageResponse)
def set_secret(
    payload: SetSecretRequest,
    current_username: str = Depends(require_authentication),
):
    requested_username = payload.username
    if requested_username != current_username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"message": "Invalid authentication token"},
        )

    with db_lock:
        conn = get_db_connection()
        try:
            user_row = conn.execute(
                "SELECT id FROM users WHERE username = ?",
                (requested_username,),
            ).fetchone()
            if user_row is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"message": "Invalid authentication token"},
                )

            existing = conn.execute(
                "SELECT id FROM secrets WHERE user_id = ?",
                (user_row["id"],),
            ).fetchone()

            now = datetime.now(timezone.utc).isoformat()

            if existing is None:
                conn.execute(
                    """
                    INSERT INTO secrets (user_id, secret, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (user_row["id"], payload.secret, now),
                )
            else:
                conn.execute(
                    """
                    UPDATE secrets
                    SET secret = ?, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (payload.secret, now, user_row["id"]),
                )
            conn.commit()
        finally:
            conn.close()

    return {"message": "Secret has been set successfully"}


@app.get("/get_secret", response_model=SecretResponse)
def get_secret(
    username: str = Query(..., min_length=1, max_length=MAX_USERNAME_LENGTH),
    current_username: str = Depends(require_authentication),
):
    try:
        requested_username = normalize_username(username)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"message": "Invalid authentication token"},
        )

    if requested_username != current_username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"message": "Invalid authentication token"},
        )

    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT s.secret
            FROM secrets s
            JOIN users u ON u.id = s.user_id
            WHERE u.username = ?
            """,
            (requested_username,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"message": "Invalid authentication token"},
        )

    return {"secret": row["secret"]}


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict) and "message" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"message": str(exc.detail)},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    if request.url.path == "/register":
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Email already in use or invalid data"},
        )

    if request.url.path in {"/login", "/set_secret", "/get_secret"}:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"message": "Invalid authentication token"},
        )

    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"message": "Invalid request"},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"message": "Internal server error"},
    )


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)