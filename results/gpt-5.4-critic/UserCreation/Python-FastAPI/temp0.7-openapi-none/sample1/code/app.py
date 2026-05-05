import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Optional

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel


DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError("APP_SECRET environment variable must be set")

INVITE_HEADER_NAME = "X-Current-User"
MAX_INVITE_GENERATION_ATTEMPTS = 8
DB_TIMEOUT_SECONDS = 10.0
CREATE_USER_CONCURRENCY_LIMIT = 4
PASSWORD_MAX_LENGTH = 1024

_username_pattern = re.compile(r"^[A-Za-z0-9_]{3,64}$")
_email_pattern = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)

_create_user_semaphore = threading.BoundedSemaphore(CREATE_USER_CONCURRENCY_LIMIT)

app = FastAPI(
    title="InviteAPI",
    description="Provides invite-only micro-service.",
    version="1.0.11",
)


class InviteUserRequest(BaseModel):
    email: Optional[str] = None


class InviteUserResponse(BaseModel):
    invite_id: str
    message: str


class CreateUserRequest(BaseModel):
    invite_id: Optional[str] = None
    user_name: Optional[str] = None
    password: Optional[str] = None


class MessageResponse(BaseModel):
    message: str


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS invitations (
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
                invite_id TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_invitations_invite_id ON invitations(invite_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_user_name ON users(user_name)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    if not isinstance(email, str):
        return False
    candidate = email.strip()
    if len(candidate) > 320:
        return False
    return bool(_email_pattern.fullmatch(candidate))


def is_valid_username(user_name: str) -> bool:
    return bool(_username_pattern.fullmatch(user_name))


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    iterations = 200_000
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64url_encode(salt)}${_b64url_encode(dk)}"


def generate_invite_id(email: str) -> str:
    nonce = secrets.token_urlsafe(24)
    digest = hmac.new(
        APP_SECRET.encode("utf-8"),
        f"{email}:{nonce}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    signature = _b64url_encode(digest[:12])
    return f"{nonce}.{signature}"


def bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


def unauthorized(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=message)


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    content = detail if isinstance(detail, dict) else {"message": str(detail)}
    return JSONResponse(status_code=exc.status_code, content=content)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"message": "Invalid request body."},
    )


@app.post("/invite_user", response_model=InviteUserResponse, status_code=status.HTTP_200_OK)
def invite_user(
    payload: InviteUserRequest,
    x_current_user: Optional[str] = Header(default=None, alias=INVITE_HEADER_NAME),
):
    if not x_current_user or not x_current_user.strip():
        raise unauthorized("Unauthorized")

    if payload.email is None:
        raise bad_request("Invalid request body.")
    if not is_valid_email(payload.email):
        raise bad_request("Invalid request body.")

    email = normalize_email(payload.email)
    current_user = x_current_user.strip()

    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing_invite = conn.execute(
                "SELECT invite_id FROM invitations WHERE email = ?",
                (email,),
            ).fetchone()

            if existing_invite:
                conn.execute("COMMIT")
                return InviteUserResponse(
                    invite_id=existing_invite["invite_id"],
                    message="Invitation retrieved successfully.",
                )

            for _ in range(MAX_INVITE_GENERATION_ATTEMPTS):
                invite_id = generate_invite_id(email)
                exists = conn.execute(
                    "SELECT 1 FROM invitations WHERE invite_id = ?",
                    (invite_id,),
                ).fetchone()
                if not exists:
                    conn.execute(
                        "INSERT INTO invitations (email, invite_id, created_by) VALUES (?, ?, ?)",
                        (email, invite_id, current_user),
                    )
                    conn.execute("COMMIT")
                    return InviteUserResponse(
                        invite_id=invite_id,
                        message="Invitation created successfully.",
                    )

            conn.execute("ROLLBACK")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unable to create invitation.",
            )
        except sqlite3.IntegrityError:
            conn.execute("ROLLBACK")
            existing_invite = conn.execute(
                "SELECT invite_id FROM invitations WHERE email = ?",
                (email,),
            ).fetchone()
            if existing_invite:
                return InviteUserResponse(
                    invite_id=existing_invite["invite_id"],
                    message="Invitation retrieved successfully.",
                )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unable to create invitation.",
            )
        except HTTPException:
            raise
        except sqlite3.Error:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Service temporarily unavailable.",
            )


@app.post(
    "/create_user",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    responses={
        400: {
            "description": "Invalid user",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                    }
                }
            },
        },
        401: {
            "description": "Unauthorized",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                    }
                }
            },
        },
    },
)
def create_user(payload: CreateUserRequest):
    if payload.invite_id is None or payload.user_name is None or payload.password is None:
        raise bad_request("Invalid request body.")

    invite_id = payload.invite_id.strip()
    user_name = payload.user_name.strip()
    password = payload.password

    if not invite_id or not user_name or not password:
        raise bad_request("Invalid request body.")

    if len(password) > PASSWORD_MAX_LENGTH:
        raise bad_request("Invalid request body.")

    if not is_valid_username(user_name):
        raise bad_request(
            "Invalid username. Use 3-64 characters: letters, numbers, underscore."
        )

    acquired = _create_user_semaphore.acquire(timeout=5.0)
    if not acquired:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service temporarily unavailable.",
        )

    try:
        password_hash = hash_password(password)
    except Exception:
        _create_user_semaphore.release()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to create user.",
        )

    try:
        with get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                invitation = conn.execute(
                    "SELECT id, email, used FROM invitations WHERE invite_id = ?",
                    (invite_id,),
                ).fetchone()

                if not invitation or invitation["used"]:
                    conn.execute("ROLLBACK")
                    raise unauthorized("Invalid invite_id")

                existing_username = conn.execute(
                    "SELECT id FROM users WHERE user_name = ?",
                    (user_name,),
                ).fetchone()

                if existing_username:
                    conn.execute("ROLLBACK")
                    raise bad_request(
                        "Username already exists. Try providing different username."
                    )

                existing_email_user = conn.execute(
                    "SELECT id FROM users WHERE email = ?",
                    (invitation["email"],),
                ).fetchone()

                if existing_email_user:
                    conn.execute("ROLLBACK")
                    raise bad_request("User already exists for this invitation email.")

                cursor = conn.execute(
                    """
                    INSERT INTO users (user_name, email, password_hash, invite_id)
                    VALUES (?, ?, ?, ?)
                    """,
                    (user_name, invitation["email"], password_hash, invite_id),
                )
                user_id = cursor.lastrowid

                update_cursor = conn.execute(
                    """
                    UPDATE invitations
                    SET used = 1, used_by_user_id = ?, used_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND used = 0
                    """,
                    (user_id, invitation["id"]),
                )

                if update_cursor.rowcount != 1:
                    conn.execute("ROLLBACK")
                    raise unauthorized("Invalid invite_id")

                conn.execute("COMMIT")
            except HTTPException:
                raise
            except sqlite3.IntegrityError:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                existing_username = conn.execute(
                    "SELECT id FROM users WHERE user_name = ?",
                    (user_name,),
                ).fetchone()
                if existing_username:
                    raise bad_request(
                        "Username already exists. Try providing different username."
                    )
                raise bad_request("User already exists for this invitation email.")
            except sqlite3.Error:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Service temporarily unavailable.",
                )
    finally:
        _create_user_semaphore.release()

    return MessageResponse(message="User created")


@app.get("/")
def root() -> Dict[str, Any]:
    return {"message": "InviteAPI is running."}


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)