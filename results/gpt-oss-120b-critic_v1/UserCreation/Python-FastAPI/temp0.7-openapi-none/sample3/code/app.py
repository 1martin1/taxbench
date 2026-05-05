import os
import uuid
import sqlite3
from datetime import datetime, timedelta
from typing import Generator, List

from fastapi import FastAPI, Depends, Request, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, constr, validator
from passlib.context import CryptContext
import uvicorn

# ------------------------------
# Configuration and Constants
# ------------------------------
DB_PATH = "db.sqlite3"
MAX_REQUEST_SIZE = 2 * 1024  # 2 KiB
RATE_LIMIT = 5  # requests
RATE_PERIOD = timedelta(minutes=1)  # per minute
MAX_PASSWORD_LENGTH = 72  # bcrypt limit
MIN_PASSWORD_LENGTH = 8
MAX_USERNAME_LENGTH = 50
MAX_EMAIL_LENGTH = 254

# ------------------------------
# FastAPI Application
# ------------------------------
app = FastAPI(
    title="InviteAPI",
    description="Provides invite-only micro-service.",
    version="1.0.11",
)

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# In‑memory rate‑limit store: {ip: [timestamp, ...]}
_rate_limit_store: dict[str, List[datetime]] = {}


# ------------------------------
# Database utilities
# ------------------------------
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Provide a SQLite connection for each request."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Create tables if they do not exist."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS invitations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                invite_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


@app.on_event("startup")
def on_startup() -> None:
    init_db()


# ------------------------------
# Request / Response models
# ------------------------------
class InviteRequest(BaseModel):
    email: EmailStr = Field(..., max_length=MAX_EMAIL_LENGTH)


class InviteResponse(BaseModel):
    invite_id: str
    message: str


class CreateUserRequest(BaseModel):
    invite_id: constr(min_length=1, max_length=64)
    user_name: constr(min_length=1, max_length=MAX_USERNAME_LENGTH)
    password: constr(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)

    @validator("user_name")
    def strip_username(cls, v: str) -> str:
        return v.strip()


class MessageResponse(BaseModel):
    message: str


# ------------------------------
# Helper functions
# ------------------------------
def generate_invite_id() -> str:
    return uuid.uuid4().hex


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


# ------------------------------
# Middleware
# ------------------------------
@app.middleware("http")
async def limit_request_size(request: Request, call_next):
    """Reject requests with bodies larger than MAX_REQUEST_SIZE."""
    body = await request.body()
    if len(body) > MAX_REQUEST_SIZE:
        return JSONResponse(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            content={"message": "Request body too large"},
        )
    # Re‑inject the body for downstream handlers
    request = Request(scope=request.scope, receive=lambda: {"type": "http.request", "body": body})
    response = await call_next(request)
    return response


# ------------------------------
# Rate limiting dependency
# ------------------------------
def rate_limiter(request: Request) -> None:
    client_ip = request.client.host if request.client else "anonymous"
    now = datetime.utcnow()
    timestamps = _rate_limit_store.get(client_ip, [])
    # Remove timestamps older than RATE_PERIOD
    timestamps = [ts for ts in timestamps if now - ts < RATE_PERIOD]
    if len(timestamps) >= RATE_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests, please try again later.",
        )
    timestamps.append(now)
    _rate_limit_store[client_ip] = timestamps


# ------------------------------
# Exception handler to match OpenAPI spec
# ------------------------------
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"message": exc.detail},
    )


# ------------------------------
# Endpoints
# ------------------------------
@app.post(
    "/invite_user",
    response_model=InviteResponse,
    dependencies=[Depends(rate_limiter)],
)
def invite_user(request: InviteRequest, db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT invite_id FROM invitations WHERE email = ?", (request.email,))
    row = cursor.fetchone()
    if row:
        invite_id = row["invite_id"]
        message = "Invitation already exists."
    else:
        invite_id = generate_invite_id()
        created_at = datetime.utcnow().isoformat()
        cursor.execute(
            "INSERT INTO invitations (email, invite_id, created_at) VALUES (?, ?, ?)",
            (request.email, invite_id, created_at),
        )
        db.commit()
        message = "Invitation created."
    return InviteResponse(invite_id=invite_id, message=message)


@app.post(
    "/create_user",
    response_model=MessageResponse,
    responses={
        400: {"model": MessageResponse},
        401: {"model": MessageResponse},
        429: {"model": MessageResponse},
    },
    dependencies=[Depends(rate_limiter)],
)
def create_user(request: CreateUserRequest, db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    # Validate invitation
    cursor.execute(
        "SELECT email FROM invitations WHERE invite_id = ?", (request.invite_id,)
    )
    invite_row = cursor.fetchone()
    if not invite_row:
        raise HTTPException(status_code=401, detail="Invalid invite_id")
    # Check username uniqueness
    cursor.execute(
        "SELECT id FROM users WHERE username = ?", (request.user_name,)
    )
    if cursor.fetchone():
        raise HTTPException(
            status_code=400,
            detail="Username already exists. Try providing different username.",
        )
    # Create user
    password_hash = hash_password(request.password)
    created_at = datetime.utcnow().isoformat()
    cursor.execute(
        "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
        (request.user_name, password_hash, created_at),
    )
    # Remove used invitation
    cursor.execute(
        "DELETE FROM invitations WHERE invite_id = ?", (request.invite_id,)
    )
    db.commit()
    return MessageResponse(message="User created successfully.")


# ------------------------------
# Application entry point
# ------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000, reload=False)