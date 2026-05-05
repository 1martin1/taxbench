import os
import sqlite3
import uuid
import hashlib
import hmac
import secrets
from contextlib import contextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, EmailStr
import uvicorn


DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET", "development-secret-change-me")


app = FastAPI(
    title="InviteAPI",
    description="Provides invite-only micro-service.",
    version="1.0.11",
)


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
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
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                used_at DATETIME,
                FOREIGN KEY (used_by_user_id) REFERENCES users(id)
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
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        (APP_SECRET + salt).encode("utf-8"),
        100_000,
    )
    return f"{salt}${derived.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, expected = stored_hash.split("$", 1)
    except ValueError:
        return False
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        (APP_SECRET + salt).encode("utf-8"),
        100_000,
    ).hex()
    return hmac.compare_digest(derived, expected)


class InviteUserRequest(BaseModel):
    email: EmailStr


class InviteUserResponse(BaseModel):
    invite_id: str
    message: str


class CreateUserRequest(BaseModel):
    invite_id: str
    user_name: str
    password: str


class MessageResponse(BaseModel):
    message: str


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.post("/invite_user", response_model=InviteUserResponse, status_code=status.HTTP_200_OK)
def invite_user(payload: InviteUserRequest):
    email = payload.email.strip().lower()

    with get_db() as conn:
        existing_invite = conn.execute(
            "SELECT invite_id FROM invites WHERE email = ?",
            (email,),
        ).fetchone()

        if existing_invite:
            return InviteUserResponse(
                invite_id=existing_invite["invite_id"],
                message="Invitation already exists for this email.",
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
                    message="Invitation already exists for this email.",
                )

        invite_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO invites (email, invite_id) VALUES (?, ?)",
            (email, invite_id),
        )

        return InviteUserResponse(
            invite_id=invite_id,
            message="Invitation created successfully.",
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
def create_user(payload: CreateUserRequest):
    invite_id = payload.invite_id.strip()
    user_name = payload.user_name.strip()
    password = payload.password

    if not invite_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid invite_id",
        )

    if not user_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists. Try providing different username.",
        )

    if not password:
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

        cursor = conn.execute(
            "INSERT INTO users (email, user_name, password_hash) VALUES (?, ?, ?)",
            (invite["email"], user_name, password_hash),
        )
        user_id = cursor.lastrowid

        conn.execute(
            """
            UPDATE invites
            SET used = 1, used_by_user_id = ?, used_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (user_id, invite["id"]),
        )

    return MessageResponse(message="User created successfully.")


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException):
    return fastapi_json_response(exc.status_code, {"message": exc.detail})


def fastapi_json_response(status_code: int, content: dict):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status_code, content=content)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)