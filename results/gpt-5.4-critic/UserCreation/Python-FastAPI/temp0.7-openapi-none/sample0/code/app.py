import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Dict, Tuple

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator


DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET")

MAX_EMAIL_LENGTH = 254
MAX_INVITE_ID_LENGTH = 128
MAX_USERNAME_LENGTH = 150
MAX_PASSWORD_LENGTH = 1024

RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_INVITE_MAX = 10
RATE_LIMIT_CREATE_MAX = 10

_rate_limit_lock = threading.Lock()
_rate_limit_store: Dict[Tuple[str, str], list[float]] = {}

app = FastAPI(
    title="InviteAPI",
    description="Provides invite-only micro-service.",
    version="1.0.11",
)


def _require_app_secret() -> str:
    if not APP_SECRET:
        raise RuntimeError("APP_SECRET environment variable must be set")
    return APP_SECRET


def _normalize_email(value: str) -> str:
    email = value.strip().lower()
    if not email or len(email) > MAX_EMAIL_LENGTH:
        raise ValueError("Invalid email")
    if "@" not in email:
        raise ValueError("Invalid email")
    local, _, domain = email.rpartition("@")
    if not local or not domain or "." not in domain:
        raise ValueError("Invalid email")
    return email


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        100_000,
    )
    return f"{base64.b64encode(salt).decode('ascii')}${digest.hex()}"


def _constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _verify_password(password: str, stored_value: str) -> bool:
    try:
        salt_b64, expected_hash = stored_value.split("$", 1)
        salt = base64.b64decode(salt_b64.encode("ascii"), validate=True)
    except (ValueError, TypeError):
        return False

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        100_000,
    ).hex()
    return _constant_time_equals(digest, expected_hash)


def _client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _check_rate_limit(bucket: str, identifier: str, limit: int) -> None:
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW_SECONDS
    key = (bucket, identifier)

    with _rate_limit_lock:
        entries = _rate_limit_store.get(key, [])
        entries = [ts for ts in entries if ts >= window_start]
        if len(entries) >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"message": "Too many requests"},
            )
        entries.append(now)
        _rate_limit_store[key] = entries


def _require_invite_auth(x_app_secret: str | None) -> None:
    secret = _require_app_secret()
    if x_app_secret is None or not hmac.compare_digest(x_app_secret, secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"message": "Unauthorized"},
        )


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS invitations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                invite_id TEXT NOT NULL UNIQUE,
                created_by TEXT,
                used INTEGER NOT NULL DEFAULT 0 CHECK (used IN (0, 1)),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                user_name TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                invite_id TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(invite_id) REFERENCES invitations(invite_id)
            )
            """
        )
        conn.commit()


class InviteUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(..., min_length=3, max_length=MAX_EMAIL_LENGTH)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _normalize_email(value)


class InviteUserResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invite_id: str
    message: str


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invite_id: str = Field(..., min_length=1, max_length=MAX_INVITE_ID_LENGTH)
    user_name: str = Field(..., min_length=1, max_length=MAX_USERNAME_LENGTH)
    password: str = Field(..., min_length=1, max_length=MAX_PASSWORD_LENGTH)

    @field_validator("invite_id")
    @classmethod
    def validate_invite_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Invalid invite_id")
        return value

    @field_validator("user_name")
    @classmethod
    def validate_user_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Invalid user")
        return value

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not value:
            raise ValueError("Invalid user")
        return value


class MessageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str


@app.on_event("startup")
def on_startup() -> None:
    _require_app_secret()
    init_db()


@app.post("/invite_user", response_model=InviteUserResponse, status_code=status.HTTP_200_OK)
def invite_user(
    payload: InviteUserRequest,
    request: Request,
    x_app_secret: str | None = Header(default=None, alias="X-APP-SECRET"),
):
    _require_invite_auth(x_app_secret)
    _check_rate_limit("invite_user", _client_ip(request), RATE_LIMIT_INVITE_MAX)

    email = payload.email

    with get_db() as conn:
        existing = conn.execute(
            "SELECT invite_id FROM invitations WHERE email = ?",
            (email,),
        ).fetchone()

        if existing:
            return {
                "invite_id": existing["invite_id"],
                "message": "Invitation already exists for this email.",
            }

        invite_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO invitations (email, invite_id, created_by, used) VALUES (?, ?, ?, 0)",
            (email, invite_id, "current_user"),
        )
        return {
            "invite_id": invite_id,
            "message": "Invitation created successfully.",
        }


@app.post(
    "/create_user",
    response_model=MessageResponse,
    responses={
        400: {"model": MessageResponse},
        401: {"model": MessageResponse},
        429: {"model": MessageResponse},
    },
    status_code=status.HTTP_200_OK,
)
def create_user(payload: CreateUserRequest, request: Request):
    _check_rate_limit("create_user", _client_ip(request), RATE_LIMIT_CREATE_MAX)

    invite_id = payload.invite_id
    user_name = payload.user_name
    password = payload.password

    password_hash = _hash_password(password)

    with get_db() as conn:
        invitation = conn.execute(
            "SELECT email, used FROM invitations WHERE invite_id = ?",
            (invite_id,),
        ).fetchone()

        if not invitation:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"message": "Invalid invite_id"},
            )

        if invitation["used"]:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"message": "Invalid invite_id"},
            )

        try:
            conn.execute(
                """
                INSERT INTO users (email, user_name, password_hash, invite_id)
                VALUES (?, ?, ?, ?)
                """,
                (invitation["email"], user_name, password_hash, invite_id),
            )
            updated = conn.execute(
                "UPDATE invitations SET used = 1 WHERE invite_id = ? AND used = 0",
                (invite_id,),
            )
            if updated.rowcount != 1:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"message": "Invalid invite_id"},
                )
        except sqlite3.IntegrityError:
            existing_username = conn.execute(
                "SELECT 1 FROM users WHERE user_name = ?",
                (user_name,),
            ).fetchone()
            if existing_username:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"message": "Username already exists. Try providing different username."},
                )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"message": "Invalid invite_id"},
            )

    return {"message": "User created successfully."}


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict) and "message" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"message": str(exc.detail)})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"message": "Internal server error"},
    )


if __name__ == "__main__":
    _require_app_secret()
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)