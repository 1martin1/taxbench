import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Dict, List, Optional

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET")
PBKDF2_ITERATIONS = 200_000

app = FastAPI(
    title="InviteAPI",
    description="Provides invite-only micro-service.",
    version="1.0.11",
)


class InMemoryRateLimiter:
    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._events: Dict[str, List[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        cutoff = now - self.window_seconds
        with self._lock:
            timestamps = self._events.get(key, [])
            timestamps = [ts for ts in timestamps if ts > cutoff]
            if len(timestamps) >= self.max_requests:
                self._events[key] = timestamps
                return False
            timestamps.append(now)
            self._events[key] = timestamps
            return True


invite_rate_limiter = InMemoryRateLimiter(max_requests=30, window_seconds=60)
create_rate_limiter = InMemoryRateLimiter(max_requests=20, window_seconds=60)


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS invitations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                invite_id TEXT NOT NULL UNIQUE,
                created_by TEXT,
                used INTEGER NOT NULL DEFAULT 0,
                used_by_user_id INTEGER,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                used_at TIMESTAMP
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
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_invitations_invite_id ON invitations(invite_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_invitations_email ON invitations(email)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_user_name ON users(user_name)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
        conn.commit()
    finally:
        conn.close()


@app.on_event("startup")
def startup_event() -> None:
    init_db()


def normalize_email(value: str) -> str:
    return value.strip().lower()


def validate_email(value: str) -> str:
    value = normalize_email(value)
    if not value or len(value) > 254:
        raise ValueError("Invalid email address.")
    if value.count("@") != 1:
        raise ValueError("Invalid email address.")
    local_part, domain = value.split("@", 1)
    if not local_part or not domain:
        raise ValueError("Invalid email address.")
    if len(local_part) > 64 or len(domain) > 253:
        raise ValueError("Invalid email address.")
    email_pattern = re.compile(
        r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
        r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
        r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
    )
    if not email_pattern.fullmatch(value):
        raise ValueError("Invalid email address.")
    return value


class InviteUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: Optional[str] = Field(default=None, examples=["user@example.com"])

    @field_validator("email")
    @classmethod
    def validate_email_field(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return validate_email(value)


class InviteUserResponse(BaseModel):
    invite_id: str
    message: str


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invite_id: Optional[str] = Field(default=None, max_length=256)
    user_name: Optional[str] = Field(default=None, max_length=150)
    password: Optional[str] = Field(default=None, max_length=1024)


class MessageResponse(BaseModel):
    message: str


def generate_invite_id() -> str:
    return secrets.token_urlsafe(24)


def hash_password(password: str) -> str:
    secret = APP_SECRET if APP_SECRET is not None else secrets.token_hex(32)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        (password + secret).encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def validate_username(user_name: str) -> bool:
    if not user_name or len(user_name) < 3 or len(user_name) > 150:
        return False
    return re.fullmatch(r"[A-Za-z0-9_.-]+", user_name) is not None


def validate_password(password: str) -> bool:
    return isinstance(password, str) and 8 <= len(password) <= 1024


def get_client_key(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
        if client_ip:
            return client_ip
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def message_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"message": message})


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
):
    return message_response(
        status.HTTP_400_BAD_REQUEST,
        "Invalid request body.",
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return message_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "Internal server error.",
    )


@app.post("/invite_user", response_model=InviteUserResponse, status_code=status.HTTP_200_OK)
def invite_user(payload: InviteUserRequest, request: Request):
    client_key = f"invite:{get_client_key(request)}"
    if not invite_rate_limiter.allow(client_key):
        return message_response(status.HTTP_401_UNAUTHORIZED, "Unauthorized")

    if payload.email is None:
        return message_response(status.HTTP_400_BAD_REQUEST, "Invalid request body.")

    email = normalize_email(payload.email)

    try:
        with get_db() as conn:
            existing = conn.execute(
                "SELECT invite_id FROM invitations WHERE email = ?",
                (email,),
            ).fetchone()

            if existing:
                invite_id = existing["invite_id"]
            else:
                invite_id = generate_invite_id()
                while conn.execute(
                    "SELECT 1 FROM invitations WHERE invite_id = ?",
                    (invite_id,),
                ).fetchone():
                    invite_id = generate_invite_id()

                try:
                    conn.execute(
                        """
                        INSERT INTO invitations (email, invite_id, created_by, used)
                        VALUES (?, ?, ?, 0)
                        """,
                        (email, invite_id, "system"),
                    )
                except sqlite3.IntegrityError:
                    row = conn.execute(
                        "SELECT invite_id FROM invitations WHERE email = ?",
                        (email,),
                    ).fetchone()
                    if row is None:
                        return message_response(
                            status.HTTP_500_INTERNAL_SERVER_ERROR,
                            "Internal server error.",
                        )
                    invite_id = row["invite_id"]

            return {
                "invite_id": invite_id,
                "message": "Invitation created successfully.",
            }
    except sqlite3.Error:
        return message_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Internal server error.",
        )


@app.post(
    "/create_user",
    response_model=MessageResponse,
    responses={
        400: {"model": MessageResponse},
        401: {"model": MessageResponse},
    },
    status_code=status.HTTP_200_OK,
)
def create_user(payload: CreateUserRequest, request: Request):
    client_key = f"create:{get_client_key(request)}"
    if not create_rate_limiter.allow(client_key):
        return message_response(status.HTTP_401_UNAUTHORIZED, "Unauthorized")

    if payload.invite_id is None or payload.user_name is None or payload.password is None:
        return message_response(status.HTTP_400_BAD_REQUEST, "Invalid request body.")

    invite_id = payload.invite_id.strip()
    user_name = payload.user_name.strip()
    password = payload.password

    if not invite_id or len(invite_id) > 256:
        return message_response(status.HTTP_401_UNAUTHORIZED, "Invalid invite_id")

    if not validate_username(user_name):
        return message_response(
            status.HTTP_400_BAD_REQUEST,
            "Invalid username. Use 3-150 characters: letters, numbers, underscore, dot, or hyphen.",
        )

    if not validate_password(password):
        return message_response(
            status.HTTP_400_BAD_REQUEST,
            "Invalid password. Password must be at least 8 characters long.",
        )

    try:
        with get_db() as conn:
            invitation = conn.execute(
                """
                SELECT id, email, invite_id, used
                FROM invitations
                WHERE invite_id = ?
                """,
                (invite_id,),
            ).fetchone()

            if not invitation or invitation["used"]:
                return message_response(
                    status.HTTP_401_UNAUTHORIZED,
                    "Invalid invite_id",
                )

            existing_username = conn.execute(
                "SELECT 1 FROM users WHERE user_name = ?",
                (user_name,),
            ).fetchone()
            if existing_username:
                return message_response(
                    status.HTTP_400_BAD_REQUEST,
                    "Username already exists. Try providing different username.",
                )

            existing_email = conn.execute(
                "SELECT 1 FROM users WHERE email = ?",
                (invitation["email"],),
            ).fetchone()
            if existing_email:
                return message_response(
                    status.HTTP_400_BAD_REQUEST,
                    "User for this invitation email already exists.",
                )

            password_hash = hash_password(password)

            try:
                cursor = conn.execute(
                    """
                    INSERT INTO users (email, user_name, password_hash, invite_id)
                    VALUES (?, ?, ?, ?)
                    """,
                    (invitation["email"], user_name, password_hash, invite_id),
                )
            except sqlite3.IntegrityError:
                duplicate_username = conn.execute(
                    "SELECT 1 FROM users WHERE user_name = ?",
                    (user_name,),
                ).fetchone()
                if duplicate_username:
                    return message_response(
                        status.HTTP_400_BAD_REQUEST,
                        "Username already exists. Try providing different username.",
                    )

                duplicate_email = conn.execute(
                    "SELECT 1 FROM users WHERE email = ?",
                    (invitation["email"],),
                ).fetchone()
                if duplicate_email:
                    return message_response(
                        status.HTTP_400_BAD_REQUEST,
                        "User for this invitation email already exists.",
                    )

                return message_response(
                    status.HTTP_401_UNAUTHORIZED,
                    "Invalid invite_id",
                )

            user_id = cursor.lastrowid

            updated = conn.execute(
                """
                UPDATE invitations
                SET used = 1, used_by_user_id = ?, used_at = CURRENT_TIMESTAMP
                WHERE id = ? AND used = 0
                """,
                (user_id, invitation["id"]),
            )

            if updated.rowcount != 1:
                return message_response(
                    status.HTTP_401_UNAUTHORIZED,
                    "Invalid invite_id",
                )

            return {"message": "User created successfully."}
    except sqlite3.Error:
        return message_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Internal server error.",
        )


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)