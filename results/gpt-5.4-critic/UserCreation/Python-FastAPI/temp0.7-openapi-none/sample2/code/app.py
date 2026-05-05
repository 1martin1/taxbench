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
from pydantic.networks import validate_email


DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET")
PBKDF2_ITERATIONS = 100_000
MAX_EMAIL_LENGTH = 320
MAX_INVITE_ID_LENGTH = 128
MAX_USERNAME_LENGTH = 150
MAX_PASSWORD_LENGTH = 1024
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_INVITE_MAX = 30
RATE_LIMIT_CREATE_MAX = 10

if not APP_SECRET:
    APP_SECRET = secrets.token_urlsafe(32)

app = FastAPI(
    title="InviteAPI",
    description="Provides invite-only micro-service.",
    version="1.0.11",
)


_rate_limit_lock = threading.Lock()
_rate_limit_store: Dict[Tuple[str, str], list[float]] = {}


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


@contextmanager
def db_cursor(write: bool = False):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if write:
            cursor.execute("BEGIN IMMEDIATE")
        yield conn, cursor
        if write:
            conn.commit()
    except Exception:
        if write:
            conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db_cursor(write=True) as (_, cursor):
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS invitations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                invite_id TEXT NOT NULL UNIQUE,
                created_by TEXT,
                used INTEGER NOT NULL DEFAULT 0,
                used_by_user_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                used_at TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_name TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                invite_id TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_invitations_email ON invitations(email)"
        )
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_invitations_invite_id ON invitations(invite_id)"
        )
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_user_name ON users(user_name)"
        )
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)"
        )
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_invite_id ON users(invite_id)"
        )


def hash_password(password: str) -> tuple[str, str]:
    salt_bytes = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt_bytes,
        PBKDF2_ITERATIONS,
    )
    return base64.b64encode(salt_bytes).decode("ascii"), derived.hex()


def generate_invite_id(email: str) -> str:
    random_part = uuid.uuid4().hex
    digest = hmac.new(
        APP_SECRET.encode("utf-8"),
        f"{email}:{random_part}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{random_part}{digest[:16]}"


def normalize_email(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid email")
    value = value.strip()
    if not value or len(value) > MAX_EMAIL_LENGTH:
        raise ValueError("Invalid email")
    _, normalized = validate_email(value)
    return normalized.lower()


def require_current_user(x_current_user: str | None) -> str:
    if x_current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"message": "Unauthorized"},
        )
    user = x_current_user.strip()
    if not user or len(user) > MAX_USERNAME_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"message": "Unauthorized"},
        )
    return user


def enforce_rate_limit(scope: str, client_key: str, limit: int) -> None:
    now = time.time()
    key = (scope, client_key)
    with _rate_limit_lock:
        entries = _rate_limit_store.get(key, [])
        entries = [ts for ts in entries if now - ts < RATE_LIMIT_WINDOW_SECONDS]
        if len(entries) >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"message": "Too many requests"},
            )
        entries.append(now)
        _rate_limit_store[key] = entries


def get_client_key(request: Request) -> str:
    client = request.client
    if client and client.host:
        return client.host
    return "unknown"


class InviteUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(..., min_length=3, max_length=MAX_EMAIL_LENGTH)

    @field_validator("email")
    @classmethod
    def validate_email_field(cls, value: str) -> str:
        return normalize_email(value)


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
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("invite_id must not be empty")
        return cleaned

    @field_validator("user_name")
    @classmethod
    def validate_user_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("user_name must not be empty")
        return cleaned

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not value:
            raise ValueError("password must not be empty")
        return value


class MessageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.post("/invite_user", response_model=InviteUserResponse, status_code=status.HTTP_200_OK)
def invite_user(
    payload: InviteUserRequest,
    request: Request,
    x_current_user: str | None = Header(default=None, alias="X-Current-User"),
):
    current_user = require_current_user(x_current_user)
    enforce_rate_limit("invite_user", get_client_key(request), RATE_LIMIT_INVITE_MAX)
    email = payload.email

    try:
        with db_cursor(write=True) as (_, cursor):
            cursor.execute(
                "SELECT invite_id FROM invitations WHERE email = ?",
                (email,),
            )
            existing = cursor.fetchone()
            if existing:
                return InviteUserResponse(
                    invite_id=existing["invite_id"],
                    message="Invitation already exists for this email.",
                )

            invite_id = generate_invite_id(email)
            cursor.execute(
                """
                INSERT INTO invitations (email, invite_id, created_by, used)
                VALUES (?, ?, ?, 0)
                """,
                (email, invite_id, current_user),
            )
            return InviteUserResponse(
                invite_id=invite_id,
                message="Invitation created successfully.",
            )
    except sqlite3.IntegrityError:
        with db_cursor() as (_, cursor):
            cursor.execute(
                "SELECT invite_id FROM invitations WHERE email = ?",
                (email,),
            )
            existing = cursor.fetchone()
            if existing:
                return InviteUserResponse(
                    invite_id=existing["invite_id"],
                    message="Invitation already exists for this email.",
                )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": "Internal server error"},
        )
    except sqlite3.Error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": "Internal server error"},
        )


@app.post("/create_user", response_model=MessageResponse, status_code=status.HTTP_200_OK)
def create_user(payload: CreateUserRequest, request: Request):
    enforce_rate_limit("create_user", get_client_key(request), RATE_LIMIT_CREATE_MAX)

    invite_id = payload.invite_id
    user_name = payload.user_name
    password = payload.password

    if not user_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Invalid user"},
        )

    if not password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Invalid user"},
        )

    try:
        with db_cursor(write=True) as (_, cursor):
            cursor.execute(
                """
                SELECT id, email
                FROM invitations
                WHERE invite_id = ? AND used = 0
                """,
                (invite_id,),
            )
            invitation = cursor.fetchone()

            if not invitation:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"message": "Invalid invite_id"},
                )

            cursor.execute(
                "SELECT id FROM users WHERE user_name = ?",
                (user_name,),
            )
            existing_user = cursor.fetchone()
            if existing_user:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"message": "Username already exists. Try providing different username."},
                )

            email = invitation["email"]

            cursor.execute(
                "SELECT id FROM users WHERE email = ?",
                (email,),
            )
            existing_email_user = cursor.fetchone()
            if existing_email_user:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"message": "Invalid user"},
                )

            salt, password_hash = hash_password(password)

            cursor.execute(
                """
                INSERT INTO users (user_name, password_hash, password_salt, invite_id, email)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_name, password_hash, salt, invite_id, email),
            )
            user_id = cursor.lastrowid

            cursor.execute(
                """
                UPDATE invitations
                SET used = 1, used_by_user_id = ?, used_at = CURRENT_TIMESTAMP
                WHERE id = ? AND used = 0
                """,
                (user_id, invitation["id"]),
            )

            if cursor.rowcount != 1:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"message": "Invalid invite_id"},
                )

        return MessageResponse(message="User created")
    except HTTPException:
        raise
    except sqlite3.IntegrityError:
        with db_cursor() as (_, cursor):
            cursor.execute(
                "SELECT id FROM users WHERE user_name = ?",
                (user_name,),
            )
            if cursor.fetchone():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"message": "Username already exists. Try providing different username."},
                )
            cursor.execute(
                "SELECT id FROM invitations WHERE invite_id = ? AND used = 1",
                (invite_id,),
            )
            if cursor.fetchone():
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"message": "Invalid invite_id"},
                )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Invalid user"},
        )
    except sqlite3.Error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": "Internal server error"},
        )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "message" in detail:
        return JSONResponse(
            status_code=exc.status_code,
            content={"message": detail["message"]},
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"message": str(detail)},
    )


@app.exception_handler(sqlite3.Error)
async def sqlite_exception_handler(request: Request, exc: sqlite3.Error):
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"message": "Internal server error"},
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