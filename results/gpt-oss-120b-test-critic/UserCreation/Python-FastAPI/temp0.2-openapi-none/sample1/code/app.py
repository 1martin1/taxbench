import os
import sqlite3
import uuid
from contextlib import contextmanager
from typing import Generator

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
import uvicorn

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET", "")

# ----------------------------------------------------------------------
# FastAPI app initialization
# ----------------------------------------------------------------------
app = FastAPI(
    title="InviteAPI",
    description="Provides invite-only micro-service.",
    version="1.0.11",
)

# ----------------------------------------------------------------------
# Password hashing
# ----------------------------------------------------------------------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ----------------------------------------------------------------------
# Database utilities
# ----------------------------------------------------------------------
def init_db() -> None:
    """Create tables if they do not exist and enforce constraints."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS invitations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                invite_id TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                invite_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (invite_id) REFERENCES invitations(invite_id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Provide a per‑request SQLite connection with foreign‑key enforcement."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


# ----------------------------------------------------------------------
# Pydantic models
# ----------------------------------------------------------------------
class InviteUserRequest(BaseModel):
    email: EmailStr


class InviteUserResponse(BaseModel):
    invite_id: str
    message: str


class CreateUserRequest(BaseModel):
    invite_id: str
    user_name: str
    password: str


class CreateUserResponse(BaseModel):
    message: str


class ErrorResponse(BaseModel):
    message: str


# ----------------------------------------------------------------------
# Exception handling – conform to OpenAPI spec (message field)
# ----------------------------------------------------------------------
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Return errors with a 'message' field instead of FastAPI's default 'detail'."""
    return JSONResponse(status_code=exc.status_code, content={"message": exc.detail})


# ----------------------------------------------------------------------
# Startup event
# ----------------------------------------------------------------------
@app.on_event("startup")
def on_startup() -> None:
    init_db()


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------
@app.post(
    "/invite_user",
    response_model=InviteUserResponse,
    responses={200: {"model": InviteUserResponse}},
)
def invite_user(
    payload: InviteUserRequest, db: sqlite3.Connection = Depends(get_db)
) -> InviteUserResponse:
    email = payload.email.lower()
    cur = db.cursor()
    cur.execute("SELECT invite_id FROM invitations WHERE email = ?", (email,))
    row = cur.fetchone()
    if row:
        invite_id = row["invite_id"]
    else:
        invite_id = uuid.uuid4().hex
        try:
            cur.execute(
                "INSERT INTO invitations (email, invite_id) VALUES (?, ?)",
                (email, invite_id),
            )
            db.commit()
        except sqlite3.IntegrityError:
            # Race condition – fetch the existing invite_id
            cur.execute("SELECT invite_id FROM invitations WHERE email = ?", (email,))
            invite_id = cur.fetchone()["invite_id"]
    return InviteUserResponse(invite_id=invite_id, message="Invitation created")


@app.post(
    "/create_user",
    response_model=CreateUserResponse,
    responses={
        200: {"model": CreateUserResponse},
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
    },
)
def create_user(
    payload: CreateUserRequest, db: sqlite3.Connection = Depends(get_db)
) -> CreateUserResponse:
    cur = db.cursor()

    # Verify invite_id exists and retrieve associated email
    cur.execute(
        "SELECT email FROM invitations WHERE invite_id = ?", (payload.invite_id,)
    )
    invite_row = cur.fetchone()
    if not invite_row:
        raise HTTPException(status_code=401, detail="Invalid invite_id")
    email = invite_row["email"]

    # Ensure username is unique
    cur.execute("SELECT id FROM users WHERE username = ?", (payload.user_name,))
    if cur.fetchone():
        raise HTTPException(
            status_code=400,
            detail="Username already exists. Try providing different username.",
        )

    # Ensure email is not already used (defensive, though invitation model should prevent this)
    cur.execute("SELECT id FROM users WHERE email = ?", (email,))
    if cur.fetchone():
        raise HTTPException(
            status_code=400,
            detail="An account with this email already exists.",
        )

    # Hash password and create user
    password_hash = pwd_context.hash(payload.password)
    try:
        cur.execute(
            """
            INSERT INTO users (username, password_hash, email, invite_id)
            VALUES (?, ?, ?, ?)
            """,
            (payload.user_name, password_hash, email, payload.invite_id),
        )
        db.commit()
    except sqlite3.IntegrityError as e:
        raise HTTPException(
            status_code=400, detail="Could not create user due to database error."
        ) from e

    return CreateUserResponse(message="User created")


# ----------------------------------------------------------------------
# Main entry point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)