import os
import sqlite3
import uuid
import hashlib
import hmac
from contextlib import contextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET", "change-me-secret")


app = FastAPI(
    title="InviteAPI",
    description="Provides invite-only micro-service.",
    version="1.0.11",
)


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
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
    salt = uuid.uuid4().hex
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        (APP_SECRET + salt).encode("utf-8"),
        100_000,
    )
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, expected = stored_hash.split("$", 1)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        (APP_SECRET + salt).encode("utf-8"),
        100_000,
    ).hex()
    return hmac.compare_digest(digest, expected)


class InviteUserRequest(BaseModel):
    email: EmailStr


class InviteUserResponse(BaseModel):
    invite_id: str
    message: str


class CreateUserRequest(BaseModel):
    invite_id: str = Field(..., min_length=1)
    user_name: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class MessageResponse(BaseModel):
    message: str


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.post("/invite_user", response_model=InviteUserResponse, status_code=status.HTTP_200_OK)
def invite_user(payload: InviteUserRequest):
    email = payload.email.lower()

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

        existing_user = conn.execute(
            "SELECT id FROM users WHERE email = ?",
            (email,),
        ).fetchone()

        if existing_user:
            existing_invite_for_user = conn.execute(
                "SELECT invite_id FROM invites WHERE email = ?",
                (email,),
            ).fetchone()
            if existing_invite_for_user:
                return InviteUserResponse(
                    invite_id=existing_invite_for_user["invite_id"],
                    message="Invitation retrieved successfully.",
                )

        invite_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO invites (email, invite_id, created_by, used) VALUES (?, ?, ?, 0)",
            (email, invite_id, None),
        )

        return InviteUserResponse(
            invite_id=invite_id,
            message="Invitation created successfully.",
        )


@app.post("/create_user", response_model=MessageResponse, status_code=status.HTTP_200_OK)
def create_user(payload: CreateUserRequest):
    invite_id = payload.invite_id.strip()
    user_name = payload.user_name.strip()
    password = payload.password

    with get_db() as conn:
        invite = conn.execute(
            "SELECT id, email, used FROM invites WHERE invite_id = ?",
            (invite_id,),
        ).fetchone()

        if not invite:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"message": "Invalid invite_id"},
            )

        if invite["used"]:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"message": "Invalid invite_id"},
            )

        existing_username = conn.execute(
            "SELECT id FROM users WHERE user_name = ?",
            (user_name,),
        ).fetchone()

        if existing_username:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"message": "Username already exists. Try providing different username."},
            )

        existing_email = conn.execute(
            "SELECT id FROM users WHERE email = ?",
            (invite["email"],),
        ).fetchone()

        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"message": "Invalid user"},
            )

        password_hash = hash_password(password)

        cursor = conn.execute(
            "INSERT INTO users (user_name, email, password_hash) VALUES (?, ?, ?)",
            (user_name, invite["email"], password_hash),
        )
        user_id = cursor.lastrowid

        conn.execute(
            "UPDATE invites SET used = 1, used_by_user_id = ?, used_at = CURRENT_TIMESTAMP WHERE id = ?",
            (user_id, invite["id"]),
        )

    return MessageResponse(message="User created successfully.")


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    from fastapi.responses import JSONResponse

    if isinstance(exc.detail, dict) and "message" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"message": str(exc.detail)})


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)