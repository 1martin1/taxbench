import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
from contextlib import closing
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, EmailStr, field_validator

DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET", "default-app-secret-change-me")
MIN_PASSWORD_LENGTH = 8


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS invites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    invite_id TEXT NOT NULL UNIQUE,
                    created_by TEXT,
                    used INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_name TEXT NOT NULL UNIQUE,
                    password TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    invite_id TEXT NOT NULL UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            conn.commit()
    except sqlite3.DatabaseError:
        # Avoid startup crash on malformed/corrupt sqlite database.
        # Endpoint handlers will surface a controlled server error if DB remains unusable.
        return


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    iterations = 200_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    salt_b64 = base64.b64encode(salt).decode("ascii")
    digest_b64 = base64.b64encode(digest).decode("ascii")
    return f"pbkdf2_sha256${iterations}${salt_b64}${digest_b64}"


def is_password_strong(password: str) -> bool:
    if len(password) < MIN_PASSWORD_LENGTH:
        return False
    has_lower = any(ch.islower() for ch in password)
    has_upper = any(ch.isupper() for ch in password)
    has_digit = any(ch.isdigit() for ch in password)
    return has_lower and has_upper and has_digit


def db_http_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Internal server error",
    )


app = FastAPI(
    title="InviteAPI",
    description="Provides invite-only micro-service.",
    version="1.0.11",
)


class InviteUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: Optional[EmailStr] = None

    @field_validator("email")
    @classmethod
    def validate_email_present(cls, value: Optional[EmailStr]) -> Optional[EmailStr]:
        if value is None:
            raise ValueError("Field required")
        return value


class InviteUserResponse(BaseModel):
    invite_id: str
    message: str


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invite_id: Optional[str] = None
    user_name: Optional[str] = None
    password: Optional[str] = None


class MessageResponse(BaseModel):
    message: str


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.post("/invite_user", response_model=InviteUserResponse, status_code=status.HTTP_200_OK)
def invite_user(payload: InviteUserRequest) -> InviteUserResponse:
    email = str(payload.email).strip().lower()

    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT invite_id FROM invites WHERE email = ?", (email,))
            existing_invite = cursor.fetchone()
            if existing_invite:
                return InviteUserResponse(
                    invite_id=existing_invite["invite_id"],
                    message="Invitation already exists for this email.",
                )

            invite_id = secrets.token_urlsafe(24)

            cursor.execute(
                """
                INSERT INTO invites (email, invite_id, created_by, used)
                VALUES (?, ?, ?, 0)
                """,
                (email, invite_id, None),
            )
            conn.commit()

            return InviteUserResponse(
                invite_id=invite_id,
                message="Invitation created successfully.",
            )
    except sqlite3.DatabaseError:
        raise db_http_exception()


@app.post(
    "/create_user",
    response_model=MessageResponse,
    responses={
        400: {"model": MessageResponse},
        401: {"model": MessageResponse},
    },
    status_code=status.HTTP_200_OK,
)
def create_user(payload: CreateUserRequest) -> MessageResponse:
    invite_id = (payload.invite_id or "").strip()
    user_name = (payload.user_name or "").strip()
    password = payload.password or ""

    if not invite_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid invite_id",
        )

    if not user_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user",
        )

    if not password or not is_password_strong(password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user",
        )

    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()

            cursor.execute(
                "SELECT email, used FROM invites WHERE invite_id = ?",
                (invite_id,),
            )
            invite = cursor.fetchone()

            if not invite or invite["used"] == 1:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid invite_id",
                )

            cursor.execute(
                "SELECT id FROM users WHERE user_name = ?",
                (user_name,),
            )
            existing_user = cursor.fetchone()
            if existing_user:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Username already exists. Try providing different username.",
                )

            cursor.execute(
                "SELECT id FROM users WHERE email = ?",
                (invite["email"],),
            )
            existing_email_user = cursor.fetchone()
            if existing_email_user:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid user",
                )

            password_hash = hash_password(password)

            try:
                cursor.execute(
                    """
                    INSERT INTO users (user_name, password, email, invite_id)
                    VALUES (?, ?, ?, ?)
                    """,
                    (user_name, password_hash, invite["email"], invite_id),
                )
                cursor.execute(
                    "UPDATE invites SET used = 1 WHERE invite_id = ?",
                    (invite_id,),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                message = str(exc).lower()
                if "users.user_name" in message or "user_name" in message:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Username already exists. Try providing different username.",
                    )
                if "users.email" in message or "email" in message:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Invalid user",
                    )
                if "users.invite_id" in message or "invite_id" in message:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid invite_id",
                    )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid user",
                )

        return MessageResponse(message="User created successfully.")
    except HTTPException:
        raise
    except sqlite3.DatabaseError:
        raise db_http_exception()


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return JSONResponse(
        status_code=exc.status_code,
        content={"message": detail},
    )


@app.exception_handler(sqlite3.DatabaseError)
async def sqlite_exception_handler(_: Request, __: sqlite3.DatabaseError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"message": "Internal server error"},
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)