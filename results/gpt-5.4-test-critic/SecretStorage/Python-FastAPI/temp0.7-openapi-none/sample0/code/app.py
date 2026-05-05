import base64
import hashlib
import hmac
import os
import sqlite3
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Generator, Optional

import uvicorn
from fastapi import Depends, FastAPI, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, ConfigDict, EmailStr, Field

DB_PATH = "db.sqlite3"
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24

_raw_app_secret = os.environ.get("APP_SECRET")
if not _raw_app_secret or not _raw_app_secret.strip():
    APP_SECRET = secrets.token_urlsafe(64)
else:
    APP_SECRET = _raw_app_secret

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)

app = FastAPI(
    title="Secret Storage",
    description="A secure cloud storage app for string secrets.",
    version="1.0.0",
)


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=8, max_length=1024)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)


class SetSecretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=150)
    secret: str = Field(min_length=1, max_length=100000)


class MessageResponse(BaseModel):
    message: str


class LoginResponse(BaseModel):
    token: str
    message: str


class SecretResponse(BaseModel):
    secret: str


def json_message(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"message": message})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    if request.url.path == "/register":
        return json_message(
            status.HTTP_400_BAD_REQUEST,
            "Email already in use or invalid data",
        )
    return json_message(
        status.HTTP_401_UNAUTHORIZED,
        "Invalid authentication token",
    )


@contextmanager
def get_db_connection() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _derive_encryption_key() -> bytes:
    return hashlib.sha256(APP_SECRET.encode("utf-8")).digest()


def encrypt_secret(plaintext: str) -> str:
    key = _derive_encryption_key()
    plaintext_bytes = plaintext.encode("utf-8")
    nonce = secrets.token_bytes(16)
    keystream = bytearray()
    counter = 0
    while len(keystream) < len(plaintext_bytes):
        block = hmac.new(
            key,
            nonce + counter.to_bytes(8, "big"),
            hashlib.sha256,
        ).digest()
        keystream.extend(block)
        counter += 1
    ciphertext = bytes(
        b ^ keystream[i] for i, b in enumerate(plaintext_bytes)
    )
    mac = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(nonce + ciphertext + mac).decode("ascii")


def decrypt_secret(token: str) -> str:
    raw = base64.urlsafe_b64decode(token.encode("ascii"))
    if len(raw) < 16 + 32:
        raise ValueError("invalid secret payload")
    nonce = raw[:16]
    mac = raw[-32:]
    ciphertext = raw[16:-32]
    key = _derive_encryption_key()
    expected_mac = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected_mac):
        raise ValueError("secret integrity check failed")

    keystream = bytearray()
    counter = 0
    while len(keystream) < len(ciphertext):
        block = hmac.new(
            key,
            nonce + counter.to_bytes(8, "big"),
            hashlib.sha256,
        ).digest()
        keystream.extend(block)
        counter += 1
    plaintext_bytes = bytes(
        b ^ keystream[i] for i, b in enumerate(ciphertext)
    )
    return plaintext_bytes.decode("utf-8")


def init_db() -> None:
    with get_db_connection() as conn:
        conn.execute("PRAGMA foreign_keys = ON")
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
    try:
        init_db()
    except sqlite3.Error as exc:
        app.state.db_init_error = str(exc)
    else:
        app.state.db_init_error = None


@app.middleware("http")
async def database_guard(request: Request, call_next):
    db_init_error = getattr(app.state, "db_init_error", None)
    if db_init_error is not None:
        return json_message(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Service temporarily unavailable",
        )
    return await call_next(request)


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
        "jti": secrets.token_hex(16),
    }
    return jwt.encode(payload, APP_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, APP_SECRET, algorithms=[JWT_ALGORITHM])


def unauthorized_exception() -> JSONResponse:
    return json_message(
        status.HTTP_401_UNAUTHORIZED,
        "Invalid authentication token",
    )


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> sqlite3.Row:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise RuntimeError("unauthorized")

    token = credentials.credentials
    try:
        payload = decode_token(token)
        user_id = int(payload.get("sub", ""))
    except (JWTError, ValueError, TypeError):
        raise RuntimeError("unauthorized")

    try:
        with get_db_connection() as conn:
            user = conn.execute(
                "SELECT id, email, username FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
    except sqlite3.Error:
        raise RuntimeError("unauthorized")

    if user is None:
        raise RuntimeError("unauthorized")

    return user


@app.exception_handler(RuntimeError)
async def runtime_error_handler(request: Request, exc: RuntimeError) -> JSONResponse:
    if str(exc) == "unauthorized":
        return unauthorized_exception()
    return json_message(status.HTTP_500_INTERNAL_SERVER_ERROR, "Internal server error")


@app.post(
    "/register",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Authentication"],
    responses={
        400: {"model": MessageResponse},
    },
)
def register(payload: RegisterRequest):
    username = payload.username.strip()
    if not username:
        return json_message(
            status.HTTP_400_BAD_REQUEST,
            "Email already in use or invalid data",
        )

    password_hash = hash_password(payload.password)
    created_at = datetime.now(timezone.utc).isoformat()

    try:
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO users (email, username, password_hash, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (payload.email.lower(), username, password_hash, created_at),
            )
    except sqlite3.IntegrityError:
        return json_message(
            status.HTTP_400_BAD_REQUEST,
            "Email already in use or invalid data",
        )
    except sqlite3.Error:
        return json_message(
            status.HTTP_400_BAD_REQUEST,
            "Email already in use or invalid data",
        )

    return MessageResponse(message="Registration successful")


@app.post(
    "/login",
    response_model=LoginResponse,
    tags=["Authentication"],
    responses={
        401: {"model": MessageResponse},
    },
)
def login(payload: LoginRequest):
    try:
        with get_db_connection() as conn:
            user = conn.execute(
                """
                SELECT id, email, username, password_hash
                FROM users
                WHERE email = ?
                """,
                (payload.email.lower(),),
            ).fetchone()
    except sqlite3.Error:
        return json_message(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid email or password",
        )

    if user is None or not verify_password(payload.password, user["password_hash"]):
        return json_message(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid email or password",
        )

    token = create_access_token(
        user_id=user["id"],
        email=user["email"],
        username=user["username"],
    )
    return LoginResponse(token=token, message="Login successful")


@app.post(
    "/set_secret",
    response_model=MessageResponse,
    responses={
        401: {"model": MessageResponse},
    },
)
def set_secret(
    payload: SetSecretRequest,
    current_user: sqlite3.Row = Depends(get_current_user),
):
    username = payload.username.strip()
    if username != current_user["username"]:
        return json_message(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid authentication token",
        )

    updated_at = datetime.now(timezone.utc).isoformat()
    encrypted_secret = encrypt_secret(payload.secret)

    try:
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO secrets (user_id, secret, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    secret = excluded.secret,
                    updated_at = excluded.updated_at
                """,
                (current_user["id"], encrypted_secret, updated_at),
            )
    except sqlite3.Error:
        return json_message(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid authentication token",
        )

    return MessageResponse(message="Secret has been set successfully")


@app.get(
    "/get_secret",
    response_model=SecretResponse,
    responses={
        401: {"model": MessageResponse},
    },
)
def get_secret(
    username: str = Query(..., min_length=1),
    current_user: sqlite3.Row = Depends(get_current_user),
):
    if username.strip() != current_user["username"]:
        return json_message(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid authentication token",
        )

    try:
        with get_db_connection() as conn:
            row = conn.execute(
                """
                SELECT s.secret
                FROM secrets s
                INNER JOIN users u ON u.id = s.user_id
                WHERE u.username = ?
                """,
                (current_user["username"],),
            ).fetchone()
    except sqlite3.Error:
        return json_message(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid authentication token",
        )

    if row is None:
        return json_message(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid authentication token",
        )

    try:
        secret = decrypt_secret(row["secret"])
    except Exception:
        return json_message(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid authentication token",
        )

    return SecretResponse(secret=secret)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)