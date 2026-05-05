import os
import re
import hmac
import time
import base64
import hashlib
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Generator, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Depends, status, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt, JWTError
from pydantic import BaseModel, Field, ConfigDict, field_validator
from passlib.context import CryptContext


DATABASE_PATH = "db.sqlite3"
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_MINUTES = 60 * 24
PBKDF2_ITERATIONS = 200_000
MAX_SECRET_LENGTH = 10000

RAW_APP_SECRET = os.getenv("APP_SECRET")
if not RAW_APP_SECRET or len(RAW_APP_SECRET) < 32:
    raise RuntimeError("APP_SECRET environment variable must be set to a strong value of at least 32 characters")

pwd_context = CryptContext(
    schemes=["pbkdf2_sha256"],
    deprecated="auto",
    pbkdf2_sha256__rounds=PBKDF2_ITERATIONS,
)
bearer_scheme = HTTPBearer(auto_error=False)

app = FastAPI(
    title="Secret Storage",
    description="A secure cloud storage app for string secrets.",
    version="1.0.0",
    openapi_tags=[{"name": "Authentication", "description": "Operations related to user authentication"}],
    openapi_extra={
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
            }
        },
        "security": [{"bearerAuth": []}],
    },
)


def derive_key(secret: str, purpose: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), purpose.encode("utf-8"), hashlib.sha256).digest()


JWT_SECRET = base64.urlsafe_b64encode(derive_key(RAW_APP_SECRET, "jwt-signing")).decode("ascii")
ENCRYPTION_KEY = derive_key(RAW_APP_SECRET, "secret-encryption")


class RateLimiter:
    def __init__(self, limit: int, window_seconds: int, block_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.block_seconds = block_seconds
        self._events = {}
        self._blocked_until = {}

    def hit(self, key: str) -> bool:
        now = time.time()
        blocked_until = self._blocked_until.get(key, 0.0)
        if blocked_until > now:
            return False

        events = self._events.get(key, [])
        events = [ts for ts in events if now - ts <= self.window_seconds]
        events.append(now)
        self._events[key] = events

        if len(events) > self.limit:
            self._blocked_until[key] = now + self.block_seconds
            return False
        return True

    def reset(self, key: str) -> None:
        self._events.pop(key, None)
        self._blocked_until.pop(key, None)


register_limiter = RateLimiter(limit=5, window_seconds=60, block_seconds=300)
login_limiter = RateLimiter(limit=5, window_seconds=60, block_seconds=300)
secret_write_limiter = RateLimiter(limit=20, window_seconds=60, block_seconds=120)
secret_read_limiter = RateLimiter(limit=30, window_seconds=60, block_seconds=120)


@contextmanager
def get_db_connection() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_db_connection() as conn:
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
                secret_encrypted TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )


@app.on_event("startup")
def startup_event() -> None:
    init_db()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_access_token(subject: str) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=JWT_EXPIRY_MINUTES)
    payload = {
        "sub": subject,
        "exp": expire,
        "iat": now,
        "type": "access",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_access_token(token: str) -> str:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token",
            )
        subject = payload.get("sub")
        if not subject or not isinstance(subject, str):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token",
            )
        return subject
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )


def xor_bytes(data: bytes, key_stream: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(data, key_stream))


def encrypt_secret(plaintext: str) -> str:
    nonce = secrets.token_bytes(16)
    plaintext_bytes = plaintext.encode("utf-8")
    keystream = b""
    counter = 0
    while len(keystream) < len(plaintext_bytes):
        block = hashlib.sha256(ENCRYPTION_KEY + nonce + counter.to_bytes(8, "big")).digest()
        keystream += block
        counter += 1
    ciphertext = xor_bytes(plaintext_bytes, keystream[: len(plaintext_bytes)])
    mac = hmac.new(ENCRYPTION_KEY, nonce + ciphertext, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(nonce + ciphertext + mac).decode("ascii")


def decrypt_secret(token: str) -> str:
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        if len(raw) < 48:
            raise ValueError("Invalid encrypted payload")
        nonce = raw[:16]
        mac = raw[-32:]
        ciphertext = raw[16:-32]
        expected_mac = hmac.new(ENCRYPTION_KEY, nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(mac, expected_mac):
            raise ValueError("MAC verification failed")
        keystream = b""
        counter = 0
        while len(keystream) < len(ciphertext):
            block = hashlib.sha256(ENCRYPTION_KEY + nonce + counter.to_bytes(8, "big")).digest()
            keystream += block
            counter += 1
        plaintext_bytes = xor_bytes(ciphertext, keystream[: len(ciphertext)])
        return plaintext_bytes.decode("utf-8")
    except (ValueError, UnicodeDecodeError, base64.binascii.Error):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )


def normalize_email(email: str) -> str:
    return email.strip().lower()


def validate_email(email: str) -> str:
    email = normalize_email(email)
    if len(email) > 254:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )
    return email


def validate_username(username: str) -> str:
    username = username.strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{3,50}", username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )
    return username


def validate_password(password: str) -> str:
    if len(password) < 8 or len(password) > 256:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )
    return password


def get_client_key(request: Request, suffix: str = "") -> str:
    client_host = request.client.host if request.client and request.client.host else "unknown"
    return f"{client_host}:{suffix}" if suffix else client_host


def get_current_username(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> str:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )
    return verify_access_token(credentials.credentials)


class MessageResponse(BaseModel):
    message: str


class TokenResponse(BaseModel):
    token: str
    message: str


class SecretResponse(BaseModel):
    secret: str


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=256)

    @field_validator("email")
    @classmethod
    def email_must_be_valid(cls, value: str) -> str:
        return validate_email(value)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    password: str = Field(min_length=1, max_length=256)

    @field_validator("email")
    @classmethod
    def email_must_be_valid(cls, value: str) -> str:
        return validate_email(value)


class SetSecretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=50)
    secret: str = Field(min_length=1, max_length=MAX_SECRET_LENGTH)


@app.post(
    "/register",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    security=[],
    responses={
        400: {
            "description": "Bad Request",
            "content": {
                "application/json": {
                    "example": {"message": "Email already in use or invalid data"}
                }
            },
        }
    },
)
def register(payload: RegisterRequest, request: Request) -> MessageResponse:
    limiter_key = get_client_key(request, "register")
    if not register_limiter.hit(limiter_key):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )

    email = validate_email(payload.email)
    username = validate_username(payload.username)
    password = validate_password(payload.password)
    password_hash = pwd_context.hash(password)

    with get_db_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE email = ? OR username = ?",
            (email, username),
        ).fetchone()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already in use or invalid data",
            )

        conn.execute(
            """
            INSERT INTO users (email, username, password_hash, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (email, username, password_hash, utc_now_iso()),
        )

    return MessageResponse(message="Registration successful")


@app.post(
    "/login",
    response_model=TokenResponse,
    security=[],
    responses={
        401: {
            "description": "Unauthorized",
            "content": {
                "application/json": {
                    "example": {"message": "Invalid email or password"}
                }
            },
        }
    },
)
def login(payload: LoginRequest, request: Request) -> TokenResponse:
    email = validate_email(payload.email)
    limiter_key = get_client_key(request, f"login:{email}")
    if not login_limiter.hit(limiter_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    with get_db_connection() as conn:
        user = conn.execute(
            "SELECT username, password_hash FROM users WHERE email = ?",
            (email,),
        ).fetchone()

    if not user or not pwd_context.verify(payload.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    login_limiter.reset(limiter_key)
    token = create_access_token(user["username"])
    return TokenResponse(token=token, message="Login successful")


@app.post(
    "/set_secret",
    response_model=MessageResponse,
    openapi_extra={"security": [{"bearerAuth": []}]},
    responses={
        401: {
            "description": "Unauthorized",
            "content": {
                "application/json": {
                    "example": {"message": "Invalid authentication token"}
                }
            },
        }
    },
)
def set_secret(
    payload: SetSecretRequest,
    request: Request,
    current_username: str = Depends(get_current_username),
) -> MessageResponse:
    limiter_key = get_client_key(request, f"set_secret:{current_username}")
    if not secret_write_limiter.hit(limiter_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    requested_username = validate_username(payload.username)
    if not hmac.compare_digest(current_username, requested_username):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    encrypted_secret = encrypt_secret(payload.secret)

    with get_db_connection() as conn:
        user = conn.execute(
            "SELECT id FROM users WHERE username = ?",
            (requested_username,),
        ).fetchone()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token",
            )

        existing = conn.execute(
            "SELECT id FROM secrets WHERE user_id = ?",
            (user["id"],),
        ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE secrets
                SET secret_encrypted = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (encrypted_secret, utc_now_iso(), user["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO secrets (user_id, secret_encrypted, updated_at)
                VALUES (?, ?, ?)
                """,
                (user["id"], encrypted_secret, utc_now_iso()),
            )

    return MessageResponse(message="Secret has been set successfully")


@app.get(
    "/get_secret",
    response_model=SecretResponse,
    openapi_extra={"security": [{"bearerAuth": []}]},
    responses={
        401: {
            "description": "Unauthorized",
            "content": {
                "application/json": {
                    "example": {"message": "Invalid authentication token"}
                }
            },
        }
    },
)
def get_secret(
    request: Request,
    username: str = Query(..., min_length=3, max_length=50),
    current_username: str = Depends(get_current_username),
) -> SecretResponse:
    limiter_key = get_client_key(request, f"get_secret:{current_username}")
    if not secret_read_limiter.hit(limiter_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    requested_username = validate_username(username)
    if not hmac.compare_digest(current_username, requested_username):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT s.secret_encrypted
            FROM secrets s
            INNER JOIN users u ON u.id = s.user_id
            WHERE u.username = ?
            """,
            (requested_username,),
        ).fetchone()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    secret = decrypt_secret(row["secret_encrypted"])
    return SecretResponse(secret=secret)


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return JSONResponse(
        status_code=exc.status_code,
        content={"message": detail},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, _: RequestValidationError):
    if request.url.path == "/register":
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Email already in use or invalid data"},
        )
    if request.url.path == "/login":
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"message": "Invalid email or password"},
        )
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"message": "Request failed"},
    )


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)