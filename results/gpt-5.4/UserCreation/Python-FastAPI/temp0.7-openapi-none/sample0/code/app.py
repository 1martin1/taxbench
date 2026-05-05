import os
import sqlite3
import secrets
from contextlib import closing
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr

DB_PATH = "db.sqlite3"


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()

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


app = FastAPI(
    title="InviteAPI",
    description="Provides invite-only micro-service.",
    version="1.0.11",
)


class InviteUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr


class InviteUserResponse(BaseModel):
    invite_id: str
    message: str


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invite_id: str
    user_name: str
    password: str


class MessageResponse(BaseModel):
    message: str


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.post("/invite_user", response_model=InviteUserResponse, status_code=status.HTTP_200_OK)
def invite_user(payload: InviteUserRequest) -> InviteUserResponse:
    email = payload.email.strip().lower()

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

        try:
            cursor.execute(
                """
                INSERT INTO users (user_name, password, email, invite_id)
                VALUES (?, ?, ?, ?)
                """,
                (user_name, password, invite["email"], invite_id),
            )
            cursor.execute(
                "UPDATE invites SET used = 1 WHERE invite_id = ?",
                (invite_id,),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already exists. Try providing different username.",
            )

    return MessageResponse(message="User created successfully.")


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException):
    from fastapi.responses import JSONResponse

    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return JSONResponse(
        status_code=exc.status_code,
        content={"message": detail},
    )


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)