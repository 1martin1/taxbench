import os
import re
import sqlite3
import secrets
from contextlib import contextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel, EmailStr, Field
from passlib.context import CryptContext


DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET", "development-secret")
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

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
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_name TEXT NOT NULL UNIQUE,
                email TEXT,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS invitations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                invite_id TEXT NOT NULL UNIQUE,
                invited_by_user_id INTEGER,
                used_by_user_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                used_at TIMESTAMP,
                FOREIGN KEY(invited_by_user_id) REFERENCES users(id),
                FOREIGN KEY(used_by_user_id) REFERENCES users(id)
            )
            """
        )


@app.on_event("startup")
def startup() -> None:
    init_db()


def get_current_user_id(x_user_name: Optional[str] = Header(default=None)) -> Optional[int]:
    if not x_user_name:
        return None
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE user_name = ?",
            (x_user_name,),
        ).fetchone()
        if row:
            return int(row["id"])
    return None


class InviteUserRequest(BaseModel):
    email: EmailStr


class InviteUserResponse(BaseModel):
    invite_id: str
    message: str


class CreateUserRequest(BaseModel):
    invite_id: str = Field(..., min_length=1)
    user_name: str = Field(..., min_length=1, max_length=150)
    password: str = Field(..., min_length=1)


class MessageResponse(BaseModel):
    message: str


def validate_username(user_name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+", user_name))


@app.post("/invite_user", response_model=InviteUserResponse)
def invite_user(
    payload: InviteUserRequest,
    current_user_id: Optional[int] = Depends(get_current_user_id),
):
    with get_db() as conn:
        existing = conn.execute(
            "SELECT invite_id FROM invitations WHERE email = ?",
            (payload.email,),
        ).fetchone()

        if existing:
            invite_id = existing["invite_id"]
            return {
                "invite_id": invite_id,
                "message": "Invitation already exists for this email.",
            }

        invite_id = secrets.token_urlsafe(24)

        conn.execute(
            """
            INSERT INTO invitations (email, invite_id, invited_by_user_id)
            VALUES (?, ?, ?)
            """,
            (payload.email, invite_id, current_user_id),
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
    },
)
def create_user(payload: CreateUserRequest):
    if not validate_username(payload.user_name):
        raise HTTPException(
            status_code=400,
            detail={"message": "Invalid username. Use only letters, numbers, underscore, dot, or hyphen."},
        )

    with get_db() as conn:
        invitation = conn.execute(
            """
            SELECT id, email, used_by_user_id
            FROM invitations
            WHERE invite_id = ?
            """,
            (payload.invite_id,),
        ).fetchone()

        if not invitation:
            raise HTTPException(
                status_code=401,
                detail={"message": "Invalid invite_id"},
            )

        if invitation["used_by_user_id"] is not None:
            raise HTTPException(
                status_code=401,
                detail={"message": "Invalid invite_id"},
            )

        existing_user = conn.execute(
            "SELECT id FROM users WHERE user_name = ?",
            (payload.user_name,),
        ).fetchone()

        if existing_user:
            raise HTTPException(
                status_code=400,
                detail={"message": "Username already exists. Try providing different username."},
            )

        password_hash = pwd_context.hash(payload.password + APP_SECRET)

        cursor = conn.execute(
            """
            INSERT INTO users (user_name, email, password_hash)
            VALUES (?, ?, ?)
            """,
            (payload.user_name, invitation["email"], password_hash),
        )
        new_user_id = cursor.lastrowid

        conn.execute(
            """
            UPDATE invitations
            SET used_by_user_id = ?, used_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (new_user_id, invitation["id"]),
        )

        return {"message": "User created"}


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    from fastapi.responses import JSONResponse

    if isinstance(exc.detail, dict) and "message" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"message": str(exc.detail)})


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)