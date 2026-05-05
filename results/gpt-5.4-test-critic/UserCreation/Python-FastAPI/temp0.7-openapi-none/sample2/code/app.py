import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, field_validator

DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET", "change-me-secret")


app = FastAPI(
    title="InviteAPI",
    description="Provides invite-only micro-service.",
    version="1.0.11",
)


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS invites (
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_name TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        (APP_SECRET + salt).encode("utf-8"),
        100_000,
    )
    return f"{salt}${digest.hex()}"


def require_current_user(x_auth_token: Optional[str]) -> str:
    if not x_auth_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )

    expected = hashlib.sha256(APP_SECRET.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(x_auth_token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )

    return "current_user"


def is_valid_email(value: str) -> bool:
    if not value or "@" not in value:
        return False
    local, _, domain = value.rpartition("@")
    if not local or not domain:
        return False
    if "." not in domain:
        return False
    return True


class InviteUserRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    email: Optional[str] = None

    @field_validator("email")
    @classmethod
    def validate_email_if_present(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = value.strip()
        if not value:
            return value
        if not is_valid_email(value):
            raise ValueError("Invalid email")
        return value


class InviteUserResponse(BaseModel):
    invite_id: str
    message: str


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    invite_id: Optional[str] = None
    user_name: Optional[str] = None
    password: Optional[str] = None


class MessageResponse(BaseModel):
    message: str


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.post("/invite_user", response_model=InviteUserResponse, status_code=status.HTTP_200_OK)
def invite_user(payload: InviteUserRequest, x_auth_token: Optional[str] = Header(default=None)):
    current_user = require_current_user(x_auth_token)

    email = (payload.email or "").strip().lower()
    if not email or not is_valid_email(email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email",
        )

    with get_db() as conn:
        existing_invite = conn.execute(
            "SELECT invite_id FROM invites WHERE email = ?",
            (email,),
        ).fetchone()

        if existing_invite:
            return InviteUserResponse(
                invite_id=existing_invite["invite_id"],
                message="Invitation retrieved successfully.",
            )

        invite_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO invites (email, invite_id, created_by, used) VALUES (?, ?, ?, 0)",
            (email, invite_id, current_user),
        )

        return InviteUserResponse(
            invite_id=invite_id,
            message="Invitation created successfully.",
        )


@app.post("/create_user", response_model=MessageResponse, status_code=status.HTTP_200_OK)
def create_user(payload: CreateUserRequest):
    invite_id = (payload.invite_id or "").strip()
    user_name = (payload.user_name or "").strip()
    password = payload.password or ""

    if not invite_id or not user_name or not password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user",
        )

    with get_db() as conn:
        invite = conn.execute(
            "SELECT id, email, used FROM invites WHERE invite_id = ?",
            (invite_id,),
        ).fetchone()

        if not invite or invite["used"]:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid invite_id",
            )

        existing_username = conn.execute(
            "SELECT id FROM users WHERE user_name = ?",
            (user_name,),
        ).fetchone()

        if existing_username:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already exists. Try providing different username.",
            )

        existing_email = conn.execute(
            "SELECT id FROM users WHERE email = ?",
            (invite["email"],),
        ).fetchone()

        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid user",
            )

        password_hash = hash_password(password)

        try:
            cursor = conn.execute(
                "INSERT INTO users (user_name, email, password_hash) VALUES (?, ?, ?)",
                (user_name, invite["email"], password_hash),
            )
            user_id = cursor.lastrowid

            conn.execute(
                "UPDATE invites SET used = 1, used_by_user_id = ?, used_at = CURRENT_TIMESTAMP WHERE id = ?",
                (user_id, invite["id"]),
            )
        except sqlite3.IntegrityError:
            existing_username = conn.execute(
                "SELECT id FROM users WHERE user_name = ?",
                (user_name,),
            ).fetchone()
            if existing_username:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Username already exists. Try providing different username.",
                )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid user",
            )

    return MessageResponse(message="User created successfully.")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict) and "message" in exc.detail:
        content = exc.detail
    else:
        content = {"message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content=content)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    path = request.url.path
    if path == "/create_user":
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Invalid user"},
        )
    if path == "/invite_user":
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Invalid email"},
        )
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"message": "Invalid request"},
    )


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)