import os
import re
import sqlite3
import secrets
import hashlib
from contextlib import contextmanager, asynccontextmanager
from typing import Optional, Any

import uvicorn
from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, field_validator
from passlib.context import CryptContext


DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    APP_SECRET = secrets.token_urlsafe(32)

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


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
                invited_by_user_id INTEGER NOT NULL,
                recipient_binding TEXT NOT NULL,
                used_by_user_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                used_at TIMESTAMP,
                FOREIGN KEY(invited_by_user_id) REFERENCES users(id),
                FOREIGN KEY(used_by_user_id) REFERENCES users(id)
            )
            """
        )

        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(invitations)").fetchall()
        }

        if "recipient_binding" not in columns:
            conn.execute(
                "ALTER TABLE invitations ADD COLUMN recipient_binding TEXT"
            )
            rows = conn.execute(
                "SELECT id, email FROM invitations WHERE recipient_binding IS NULL"
            ).fetchall()
            for row in rows:
                binding = hashlib.sha256(
                    f"{APP_SECRET}:{row['email'].strip().lower()}".encode("utf-8")
                ).hexdigest()
                conn.execute(
                    "UPDATE invitations SET recipient_binding = ? WHERE id = ?",
                    (binding, row["id"]),
                )

        if "invited_by_user_id" in columns:
            conn.execute(
                """
                DELETE FROM invitations
                WHERE invited_by_user_id IS NULL
                """
            )

        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_invitations_email_unique
            ON invitations(email)
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_invitations_invite_id_unique
            ON invitations(invite_id)
            """
        )


def normalize_email(email: str) -> str:
    return email.strip().lower()


def make_recipient_binding(email: str) -> str:
    normalized = normalize_email(email)
    return hashlib.sha256(f"{APP_SECRET}:{normalized}".encode("utf-8")).hexdigest()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="InviteAPI",
    description="Provides invite-only micro-service.",
    version="1.0.11",
    lifespan=lifespan,
)


def get_current_user_id(x_user_name: Optional[str] = Header(default=None)) -> int:
    if not x_user_name:
        raise HTTPException(status_code=401, detail={"message": "Unauthorized"})
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE user_name = ?",
            (x_user_name,),
        ).fetchone()
        if row:
            return int(row["id"])
    raise HTTPException(status_code=401, detail={"message": "Unauthorized"})


class InviteUserRequest(BaseModel):
    email: Optional[EmailStr] = None


class InviteUserResponse(BaseModel):
    invite_id: str
    message: str


class CreateUserRequest(BaseModel):
    invite_id: Optional[str] = None
    user_name: Optional[str] = None
    password: Optional[str] = None

    @field_validator("invite_id", "user_name", "password")
    @classmethod
    def strip_if_str(cls, value: Optional[str]) -> Optional[str]:
        if isinstance(value, str):
            return value.strip()
        return value


class MessageResponse(BaseModel):
    message: str


def validate_username(user_name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+", user_name))


def require_non_empty_string(value: Optional[str], field_name: str) -> str:
    if value is None or value == "":
        raise HTTPException(
            status_code=400,
            detail={"message": f"Invalid {field_name}"},
        )
    return value


@app.post("/invite_user", response_model=InviteUserResponse)
def invite_user(
    payload: InviteUserRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    if payload.email is None:
        raise HTTPException(status_code=400, detail={"message": "Invalid email"})

    normalized_email = normalize_email(str(payload.email))
    recipient_binding = make_recipient_binding(normalized_email)

    with get_db() as conn:
        existing = conn.execute(
            "SELECT invite_id FROM invitations WHERE email = ?",
            (normalized_email,),
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
            INSERT INTO invitations (email, invite_id, invited_by_user_id, recipient_binding)
            VALUES (?, ?, ?, ?)
            """,
            (normalized_email, invite_id, current_user_id, recipient_binding),
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
def create_user(
    payload: CreateUserRequest,
    x_user_name: Optional[str] = Header(default=None),
):
    invite_id = require_non_empty_string(payload.invite_id, "invite_id")
    user_name = require_non_empty_string(payload.user_name, "user_name")
    password = require_non_empty_string(payload.password, "password")

    if not validate_username(user_name):
        raise HTTPException(
            status_code=400,
            detail={"message": "Invalid username. Use only letters, numbers, underscore, dot, or hyphen."},
        )

    if not x_user_name or x_user_name.strip() != user_name:
        raise HTTPException(
            status_code=401,
            detail={"message": "Invalid invite_id"},
        )

    with get_db() as conn:
        invitation = conn.execute(
            """
            SELECT id, email, recipient_binding, used_by_user_id
            FROM invitations
            WHERE invite_id = ?
            """,
            (invite_id,),
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

        expected_binding = make_recipient_binding(invitation["email"])
        if (
            not invitation["recipient_binding"]
            or invitation["recipient_binding"] != expected_binding
        ):
            raise HTTPException(
                status_code=401,
                detail={"message": "Invalid invite_id"},
            )

        existing_user = conn.execute(
            "SELECT id FROM users WHERE user_name = ?",
            (user_name,),
        ).fetchone()

        if existing_user:
            raise HTTPException(
                status_code=400,
                detail={"message": "Username already exists. Try providing different username."},
            )

        password_hash = pwd_context.hash(password + APP_SECRET)

        cursor = conn.execute(
            """
            INSERT INTO users (user_name, email, password_hash)
            VALUES (?, ?, ?)
            """,
            (user_name, invitation["email"], password_hash),
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
async def http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict) and "message" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"message": str(exc.detail)})


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    path = request.url.path
    if path == "/create_user":
        return JSONResponse(status_code=400, content={"message": "Invalid user"})
    if path == "/invite_user":
        return JSONResponse(status_code=400, content={"message": "Invalid email"})
    return JSONResponse(status_code=400, content={"message": "Invalid request"})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"message": "Internal server error"})


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)