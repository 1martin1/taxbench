import os
import sqlite3
import secrets
from contextlib import contextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from passlib.context import CryptContext

DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET", "development-secret")

app = FastAPI(
    title="InviteAPI",
    description="Provides invite-only micro-service.",
    version="1.0.11",
)

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


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
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
                invite_id TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


@app.on_event("startup")
def startup_event() -> None:
    init_db()


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


def generate_invite_id() -> str:
    return secrets.token_urlsafe(24)


@app.post("/invite_user", response_model=InviteUserResponse, status_code=status.HTTP_200_OK)
def invite_user(payload: InviteUserRequest):
    email = payload.email.lower()

    with get_db() as conn:
        existing = conn.execute(
            "SELECT invite_id FROM invites WHERE email = ?",
            (email,),
        ).fetchone()

        if existing:
            return InviteUserResponse(
                invite_id=existing["invite_id"],
                message="Invitation retrieved successfully.",
            )

        invite_id = generate_invite_id()
        while conn.execute(
            "SELECT 1 FROM invites WHERE invite_id = ?",
            (invite_id,),
        ).fetchone():
            invite_id = generate_invite_id()

        conn.execute(
            """
            INSERT INTO invites (email, invite_id, created_by, used)
            VALUES (?, ?, ?, 0)
            """,
            (email, invite_id, APP_SECRET),
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

    if not user_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Username already exists. Try providing different username."},
        )

    with get_db() as conn:
        invite = conn.execute(
            "SELECT * FROM invites WHERE invite_id = ?",
            (invite_id,),
        ).fetchone()

        if not invite or invite["used"]:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"message": "Invalid invite_id"},
            )

        existing_user = conn.execute(
            "SELECT 1 FROM users WHERE user_name = ?",
            (user_name,),
        ).fetchone()

        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"message": "Username already exists. Try providing different username."},
            )

        existing_email_user = conn.execute(
            "SELECT 1 FROM users WHERE email = ?",
            (invite["email"],),
        ).fetchone()

        if existing_email_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"message": "Invalid user"},
            )

        password_hash = pwd_context.hash(password)

        cursor = conn.execute(
            """
            INSERT INTO users (user_name, email, password_hash, invite_id)
            VALUES (?, ?, ?, ?)
            """,
            (user_name, invite["email"], password_hash, invite_id),
        )
        user_id = cursor.lastrowid

        conn.execute(
            """
            UPDATE invites
            SET used = 1, used_by_user_id = ?, used_at = CURRENT_TIMESTAMP
            WHERE invite_id = ?
            """,
            (user_id, invite_id),
        )

        return MessageResponse(message="User created")


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException):
    if isinstance(exc.detail, dict) and "message" in exc.detail:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=exc.status_code, content={"message": str(exc.detail)})


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)