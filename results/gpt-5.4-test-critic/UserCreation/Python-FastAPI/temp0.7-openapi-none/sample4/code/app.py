import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager, contextmanager

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field

DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET")

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
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
                created_by TEXT NOT NULL,
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


def require_app_secret() -> str:
    if not APP_SECRET:
        raise RuntimeError("APP_SECRET environment variable must be set")
    return APP_SECRET


def unauthorized_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"message": "Unauthorized"},
    )


def verify_auth_header(authorization: str | None) -> str:
    secret = require_app_secret()
    if not authorization:
        raise unauthorized_exception()

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise unauthorized_exception()

    if not secrets.compare_digest(token, secret):
        raise unauthorized_exception()

    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


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


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="InviteAPI",
    description="Provides invite-only micro-service.",
    version="1.0.11",
    lifespan=lifespan,
)


@app.post("/invite_user", response_model=InviteUserResponse, status_code=status.HTTP_200_OK)
def invite_user(payload: InviteUserRequest, authorization: str | None = Header(default=None)):
    created_by = verify_auth_header(authorization)
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
            (email, invite_id, created_by),
        )

        return InviteUserResponse(
            invite_id=invite_id,
            message="Invitation created successfully.",
        )


@app.post("/create_user", response_model=MessageResponse, status_code=status.HTTP_200_OK)
def create_user(payload: CreateUserRequest, authorization: str | None = Header(default=None)):
    verify_auth_header(authorization)

    invite_id = payload.invite_id.strip()
    user_name = payload.user_name.strip()
    password = payload.password

    if not invite_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Invalid user"},
        )

    if not user_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Invalid user"},
        )

    if not password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Invalid user"},
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

        try:
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
                WHERE invite_id = ? AND used = 0
                """,
                (user_id, invite_id),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"message": "Invalid user"},
            )

        return MessageResponse(message="User created")


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    if isinstance(exc.detail, dict) and "message" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"message": str(exc.detail)})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError):
    errors = exc.errors()
    has_email_error = any(
        err.get("loc") and "email" in err.get("loc", []) for err in errors
    )

    if has_email_error:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Invalid user"},
        )

    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"message": "Invalid user"},
    )


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)