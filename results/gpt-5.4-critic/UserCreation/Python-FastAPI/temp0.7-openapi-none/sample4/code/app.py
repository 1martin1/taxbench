import os
import re
import sqlite3
import uuid
import hashlib
import hmac
import secrets
import threading
import time
from contextlib import contextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, field_validator


DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET")
PBKDF2_ITERATIONS = 100_000
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_INVITE = 30
RATE_LIMIT_MAX_CREATE = 10
RATE_LIMIT_MAX_GLOBAL = 120

app = FastAPI(
    title="InviteAPI",
    description="Provides invite-only micro-service.",
    version="1.0.11",
)


class SimpleRateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, list[float]] = {}

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        cutoff = now - window_seconds
        with self._lock:
            entries = self._events.get(key, [])
            entries = [ts for ts in entries if ts >= cutoff]
            if len(entries) >= limit:
                self._events[key] = entries
                return False
            entries.append(now)
            self._events[key] = entries
            return True


rate_limiter = SimpleRateLimiter()


def json_message_response(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(content={"message": message}, status_code=status_code)


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_name TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS invites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                invite_id TEXT NOT NULL UNIQUE,
                created_by TEXT,
                used INTEGER NOT NULL DEFAULT 0,
                used_by_user_id INTEGER,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                used_at TIMESTAMP,
                FOREIGN KEY (used_by_user_id) REFERENCES users(id)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        yield conn
    finally:
        conn.close()


def generate_invite_id() -> str:
    return str(uuid.uuid4())


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


def verify_password(password: str, expected_hash: str, salt: str) -> bool:
    computed_hash, _ = hash_password(password, salt)
    return hmac.compare_digest(computed_hash, expected_hash)


def validate_username(user_name: str) -> bool:
    if not user_name:
        return False
    if len(user_name) < 3 or len(user_name) > 150:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+", user_name))


def get_client_ip(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def enforce_rate_limit(request: Request, endpoint_key: str, limit: int) -> None:
    client_ip = get_client_ip(request)
    if not rate_limiter.allow(f"global:{client_ip}", RATE_LIMIT_MAX_GLOBAL, RATE_LIMIT_WINDOW_SECONDS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Too many requests. Please try again later.",
        )
    if not rate_limiter.allow(f"{endpoint_key}:{client_ip}", limit, RATE_LIMIT_WINDOW_SECONDS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Too many requests. Please try again later.",
        )


class InviteUserRequest(BaseModel):
    email: Optional[EmailStr] = None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: Optional[EmailStr]) -> Optional[str]:
        if value is None:
            return value
        return str(value).strip().lower()


class InviteUserResponse(BaseModel):
    invite_id: str
    message: str


class CreateUserRequest(BaseModel):
    invite_id: Optional[str] = None
    user_name: Optional[str] = None
    password: Optional[str] = None


class MessageResponse(BaseModel):
    message: str


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.post("/invite_user", response_model=InviteUserResponse, status_code=status.HTTP_200_OK)
def invite_user(payload: InviteUserRequest, request: Request):
    enforce_rate_limit(request, "invite_user", RATE_LIMIT_MAX_INVITE)

    if payload.email is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="email is required",
        )

    normalized_email = payload.email

    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT invite_id FROM invites WHERE email = ?",
            (normalized_email,),
        ).fetchone()

        if existing:
            conn.execute("COMMIT")
            return InviteUserResponse(
                invite_id=existing["invite_id"],
                message="Invitation already exists for this email.",
            )

        invite_id = generate_invite_id()
        created_by = APP_SECRET if APP_SECRET else None

        try:
            conn.execute(
                """
                INSERT INTO invites (email, invite_id, created_by, used)
                VALUES (?, ?, ?, 0)
                """,
                (normalized_email, invite_id, created_by),
            )
            conn.execute("COMMIT")
        except sqlite3.IntegrityError:
            conn.execute("ROLLBACK")
            existing = conn.execute(
                "SELECT invite_id FROM invites WHERE email = ?",
                (normalized_email,),
            ).fetchone()
            if existing:
                return InviteUserResponse(
                    invite_id=existing["invite_id"],
                    message="Invitation already exists for this email.",
                )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Request failed.",
            )
        except sqlite3.OperationalError:
            conn.execute("ROLLBACK")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Request failed.",
            )

        return InviteUserResponse(
            invite_id=invite_id,
            message="Invitation created successfully.",
        )


@app.post(
    "/create_user",
    response_model=MessageResponse,
    responses={
        400: {
            "description": "Invalid user",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                    }
                }
            },
        },
        401: {
            "description": "Unauthorized",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                    }
                }
            },
        },
    },
    status_code=status.HTTP_200_OK,
)
def create_user(payload: CreateUserRequest, request: Request):
    enforce_rate_limit(request, "create_user", RATE_LIMIT_MAX_CREATE)

    if payload.invite_id is None or payload.user_name is None or payload.password is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invite_id, user_name, and password are required.",
        )

    if not validate_username(payload.user_name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid username. Use 3-150 characters: letters, numbers, underscore, dot, or hyphen.",
        )

    if len(payload.password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid password. Password must be at least 8 characters long.",
        )

    with get_db() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")

            invite = conn.execute(
                "SELECT id, used FROM invites WHERE invite_id = ?",
                (payload.invite_id,),
            ).fetchone()

            if not invite or invite["used"]:
                conn.execute("ROLLBACK")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid invite_id",
                )

            existing_user = conn.execute(
                "SELECT id FROM users WHERE user_name = ?",
                (payload.user_name,),
            ).fetchone()

            if existing_user:
                conn.execute("ROLLBACK")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Username already exists. Try providing different username.",
                )

            password_hash, password_salt = hash_password(payload.password)

            cursor = conn.execute(
                """
                INSERT INTO users (user_name, password_hash, password_salt)
                VALUES (?, ?, ?)
                """,
                (payload.user_name, password_hash, password_salt),
            )
            user_id = cursor.lastrowid

            updated = conn.execute(
                """
                UPDATE invites
                SET used = 1, used_by_user_id = ?, used_at = CURRENT_TIMESTAMP
                WHERE id = ? AND used = 0
                """,
                (user_id, invite["id"]),
            )

            if updated.rowcount != 1:
                conn.execute("ROLLBACK")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid invite_id",
                )

            conn.execute("COMMIT")
            return MessageResponse(message="User created successfully.")
        except sqlite3.IntegrityError:
            conn.execute("ROLLBACK")
            existing_user = conn.execute(
                "SELECT id FROM users WHERE user_name = ?",
                (payload.user_name,),
            ).fetchone()
            if existing_user:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Username already exists. Try providing different username.",
                )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Request failed.",
            )
        except sqlite3.OperationalError:
            conn.execute("ROLLBACK")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Request failed.",
            )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, str):
        return json_message_response(exc.detail, exc.status_code)
    return json_message_response("Request failed.", exc.status_code)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    return json_message_response("Invalid request body.", status.HTTP_400_BAD_REQUEST)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)