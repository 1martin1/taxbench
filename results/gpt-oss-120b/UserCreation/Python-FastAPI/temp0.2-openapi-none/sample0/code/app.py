import os
import uuid
import sqlite3
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, ValidationError
from passlib.context import CryptContext
from starlette.middleware.base import BaseHTTPMiddleware

# Initialize FastAPI app
app = FastAPI(title="InviteAPI", version="1.0.11")

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Database file
DB_PATH = "db.sqlite3"


def get_db():
    """Dependency that provides a DB connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Create tables if they don't exist."""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS invitations (
                email TEXT PRIMARY KEY,
                invite_id TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_name TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


# Run DB init on startup
@app.on_event("startup")
def on_startup():
    init_db()


# Pydantic models for request/response
class InviteUserRequest(BaseModel):
    email: EmailStr = Field(..., example="user@example.com")


class InviteUserResponse(BaseModel):
    invite_id: str
    message: str = "Invitation prepared"


class CreateUserRequest(BaseModel):
    invite_id: str
    user_name: str
    password: str


class MessageResponse(BaseModel):
    message: str


# Middleware to handle validation errors uniformly
class ValidationExceptionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except ValidationError as exc:
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content={"detail": exc.errors()},
            )


app.add_middleware(ValidationExceptionMiddleware)


@app.post(
    "/invite_user",
    response_model=InviteUserResponse,
    summary="Create an invitation by the current user to create a new user.",
    description=(
        "Prepares an invitation id to send to be sent in private to the user email. "
        "The new user then can only create its account by giving the provided id. "
        "Only one invitation id can be created per email. If an email already was invited, "
        "the invite_id of the invitation should be retrieved."
    ),
)
def invite_user(payload: InviteUserRequest, db: sqlite3.Connection = Depends(get_db)):
    email = payload.email.lower()
    cur = db.cursor()
    cur.execute(
        "SELECT invite_id FROM invitations WHERE email = ?", (email,)
    )
    row = cur.fetchone()
    if row:
        invite_id = row["invite_id"]
    else:
        invite_id = uuid.uuid4().hex
        created_at = datetime.utcnow().isoformat()
        cur.execute(
            "INSERT INTO invitations (email, invite_id, created_at) VALUES (?, ?, ?)",
            (email, invite_id, created_at),
        )
        db.commit()
    return InviteUserResponse(invite_id=invite_id)


@app.post(
    "/create_user",
    response_model=MessageResponse,
    responses={
        400: {"model": MessageResponse, "description": "Invalid user"},
        401: {"model": MessageResponse, "description": "Unauthorized"},
    },
    summary="Given an invitation, create a user.",
    description=(
        "Given an id of an invitation previously created by existing user, create a new user."
    ),
)
def create_user(
    payload: CreateUserRequest, db: sqlite3.Connection = Depends(get_db)
):
    cur = db.cursor()
    # Verify invite_id exists
    cur.execute(
        "SELECT email FROM invitations WHERE invite_id = ?", (payload.invite_id,)
    )
    invite_row = cur.fetchone()
    if not invite_row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"message": "Invalid invite_id"},
        )

    # Check username uniqueness
    cur.execute(
        "SELECT id FROM users WHERE user_name = ?", (payload.user_name,)
    )
    if cur.fetchone():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Username already exists. Try providing different username."},
        )

    # Hash password and create user
    password_hash = pwd_context.hash(payload.password)
    created_at = datetime.utcnow().isoformat()
    cur.execute(
        "INSERT INTO users (user_name, password_hash, created_at) VALUES (?, ?, ?)",
        (payload.user_name, password_hash, created_at),
    )
    # Optionally delete the invitation after use
    cur.execute(
        "DELETE FROM invitations WHERE invite_id = ?", (payload.invite_id,)
    )
    db.commit()
    return MessageResponse(message="User created")


# Run the application with uvicorn when executed directly
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=5000,
        log_level="info",
    )