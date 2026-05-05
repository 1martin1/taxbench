import os
import uuid
import sqlite3
import time
from datetime import datetime
from typing import Generator

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, EmailStr, Field, validator

from passlib.context import CryptContext

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
DB_PATH = "db.sqlite3"
MAX_BODY_SIZE = 10 * 1024  # 10 KB
RATE_LIMIT_CALLS = 5       # max calls
RATE_LIMIT_PERIOD = 60     # per 60 seconds

# ----------------------------------------------------------------------
# Security utilities
# ----------------------------------------------------------------------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


# ----------------------------------------------------------------------
# Database utilities
# ----------------------------------------------------------------------
def get_connection() -> sqlite3.Connection:
    """Create a new SQLite connection with sensible pragmas."""
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def init_db() -> None:
    """Initialize the database schema."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS invitations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            invite_id TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            invited_by INTEGER,
            FOREIGN KEY(invited_by) REFERENCES invitations(id)
        )
        """
    )
    conn.commit()
    conn.close()


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """FastAPI dependency that yields a fresh DB connection per request."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


# ----------------------------------------------------------------------
# Pydantic models
# ----------------------------------------------------------------------
class InviteUserRequest(BaseModel):
    email: EmailStr = Field(..., example="user@example.com")

    @validator("email")
    def email_length(cls, v: str) -> str:
        if len(v) > 254:
            raise ValueError("Email length exceeds 254 characters")
        return v


class InviteUserResponse(BaseModel):
    invite_id: str
    message: str = "Invitation created or retrieved successfully."


class CreateUserRequest(BaseModel):
    invite_id: str
    user_name: str = Field(..., alias="user_name", min_length=1, max_length=64)
    password: str = Field(..., min_length=8, max_length=128)

    @validator("user_name")
    def username_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Username cannot be empty or whitespace")
        return v


class CreateUserSuccessResponse(BaseModel):
    message: str = "User created successfully."


class ErrorResponse(BaseModel):
    message: str


# ----------------------------------------------------------------------
# Middleware
# ----------------------------------------------------------------------
class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests with a body larger than MAX_BODY_SIZE."""

    def __init__(self, app, max_body_size: int = MAX_BODY_SIZE):
        super().__init__(app)
        self.max_body_size = max_body_size

    async def dispatch(self, request: Request, call_next):
        body = await request.body()
        if len(body) > self.max_body_size:
            return JSONResponse(
                status_code=413,
                content={"message": "Payload too large"},
            )
        # Re‑inject the body so downstream handlers can read it again
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request._receive = receive  # type: ignore
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in‑memory rate limiter per client IP + endpoint."""

    def __init__(self, app, calls: int = RATE_LIMIT_CALLS, period: int = RATE_LIMIT_PERIOD):
        super().__init__(app)
        self.calls = calls
        self.period = period
        self.storage: dict[tuple[str, str], list[float]] = {}

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "anonymous"
        path = request.url.path
        key = (client_ip, path)
        now = time.time()
        timestamps = self.storage.get(key, [])
        # Keep only timestamps within the period
        timestamps = [ts for ts in timestamps if now - ts < self.period]
        if len(timestamps) >= self.calls:
            return JSONResponse(
                status_code=429,
                content={"message": "Too Many Requests"},
            )
        timestamps.append(now)
        self.storage[key] = timestamps
        return await call_next(request)


# ----------------------------------------------------------------------
# FastAPI application
# ----------------------------------------------------------------------
app = FastAPI(
    title="InviteAPI",
    version="1.0.11",
    description="Provides invite-only micro-service.",
)

# Register middlewares
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(RateLimitMiddleware)


@app.on_event("startup")
def on_startup():
    init_db()


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------
@app.post("/invite_user", response_model=InviteUserResponse)
def invite_user(
    request: InviteUserRequest,
    db: sqlite3.Connection = Depends(get_db),
):
    email = request.email.lower()
    cur = db.cursor()
    cur.execute("SELECT invite_id FROM invitations WHERE email = ?", (email,))
    row = cur.fetchone()
    if row:
        invite_id = row["invite_id"]
    else:
        invite_id = str(uuid.uuid4())
        created_at = datetime.utcnow().isoformat()
        try:
            cur.execute(
                "INSERT INTO invitations (email, invite_id, created_at) VALUES (?, ?, ?)",
                (email, invite_id, created_at),
            )
            db.commit()
        except sqlite3.IntegrityError:
            # Race condition – another request inserted the same email
            cur.execute("SELECT invite_id FROM invitations WHERE email = ?", (email,))
            row = cur.fetchone()
            if row:
                invite_id = row["invite_id"]
            else:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to create invitation due to a database error.",
                )
    return InviteUserResponse(invite_id=invite_id)


@app.post(
    "/create_user",
    responses={
        200: {"model": CreateUserSuccessResponse},
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
    },
)
def create_user(
    request: CreateUserRequest,
    db: sqlite3.Connection = Depends(get_db),
):
    cur = db.cursor()
    # Verify invitation exists
    cur.execute(
        "SELECT id FROM invitations WHERE invite_id = ?", (request.invite_id,)
    )
    invite_row = cur.fetchone()
    if not invite_row:
        raise HTTPException(status_code=401, detail="Invalid invite_id")
    invite_id_db = invite_row["id"]

    # Check username uniqueness
    cur.execute("SELECT id FROM users WHERE username = ?", (request.user_name,))
    if cur.fetchone():
        raise HTTPException(
            status_code=400,
            detail="Username already exists. Try providing different username.",
        )

    # Create user
    password_hash = hash_password(request.password)
    created_at = datetime.utcnow().isoformat()
    try:
        cur.execute(
            """
            INSERT INTO users (username, password_hash, created_at, invited_by)
            VALUES (?, ?, ?, ?)
            """,
            (request.user_name, password_hash, created_at, invite_id_db),
        )
        # Delete invitation to prevent reuse
        cur.execute("DELETE FROM invitations WHERE id = ?", (invite_id_db,))
        db.commit()
    except sqlite3.IntegrityError as e:
        raise HTTPException(status_code=400, detail="Failed to create user.") from e

    return CreateUserSuccessResponse()


# ----------------------------------------------------------------------
# Custom exception handler matching the OpenAPI schema
# ----------------------------------------------------------------------
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # Do not expose internal details; use the provided message only.
    return JSONResponse(
        status_code=exc.status_code,
        content={"message": exc.detail},
    )


# ----------------------------------------------------------------------
# Run the application
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # Load secret for potential future extensions (not used directly here)
    _ = os.getenv("APP_SECRET", "default_secret")
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)