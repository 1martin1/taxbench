import os
import uuid
import datetime
import sqlite3
import threading
import time
from typing import Optional, List, Dict, Tuple

from fastapi import (
    FastAPI,
    HTTPException,
    Depends,
    status,
    Request,
    Header,
)
from fastapi.responses import JSONResponse
from fastapi.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, EmailStr, Field, validator
from passlib.context import CryptContext

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError("APP_SECRET environment variable is required")

DB_PATH = "db.sqlite3"
MAX_BODY_SIZE = 1 * 1024 * 1024  # 1 MB
RATE_LIMIT = 60  # requests
RATE_PERIOD = 60  # seconds
INVITE_EXPIRATION_DAYS = 30
PASSWORD_MAX_LENGTH = 128
USERNAME_REGEX = r'^[A-Za-z0-9_]+$'

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="InviteAPI",
    version="1.0.11",
    description="Provides invite-only micro-service."
)

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ---------------------------------------------------------------------------
# Database utilities
# ---------------------------------------------------------------------------
def get_db():
    """Dependency that provides a SQLite connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Create required tables if they do not exist."""
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
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                email TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def delete_expired_invitations(conn: sqlite3.Connection):
    """Remove invitations older than INVITE_EXPIRATION_DAYS."""
    threshold = (datetime.datetime.utcnow() -
                 datetime.timedelta(days=INVITE_EXPIRATION_DAYS)).isoformat()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM invitations WHERE created_at < ?",
        (threshold,)
    )
    conn.commit()


def background_cleanup():
    """Background thread that periodically cleans up old invitations."""
    while True:
        try:
            with sqlite3.connect(DB_PATH) as conn:
                delete_expired_invitations(conn)
        except Exception:
            pass  # Swallow errors; cleanup will retry later
        time.sleep(3600)  # Run every hour


@app.on_event("startup")
def on_startup():
    init_db()
    # Start cleanup thread
    thread = threading.Thread(target=background_cleanup, daemon=True)
    thread.start()


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        body = await request.body()
        if len(body) > MAX_BODY_SIZE:
            return JSONResponse(
                {"detail": "Request body too large"},
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        # Re-create request with the same body for downstream handlers
        async def receive():
            return {"type": "http.request", "body": body}
        request._receive = receive  # type: ignore
        return await call_next(request)


app.add_middleware(BodySizeLimitMiddleware)

# ---------------------------------------------------------------------------
# Simple in‑memory rate limiter
# ---------------------------------------------------------------------------
rate_limiter_lock = threading.Lock()
rate_limiter_store: Dict[Tuple[str, str], List[float]] = {}  # (ip, path) -> timestamps


def rate_limiter(request: Request):
    client_ip = request.client.host
    path = request.url.path
    now = time.time()
    key = (client_ip, path)
    with rate_limiter_lock:
        timestamps = rate_limiter_store.get(key, [])
        # Remove timestamps outside the window
        timestamps = [ts for ts in timestamps if now - ts < RATE_PERIOD]
        if len(timestamps) >= RATE_LIMIT:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests, please try again later."
            )
        timestamps.append(now)
        rate_limiter_store[key] = timestamps


@app.middleware("http")
async def add_rate_limit(request: Request, call_next):
    # Apply rate limiting to the two public endpoints
    if request.url.path in {"/invite_user", "/create_user"}:
        rate_limiter(request)
    response = await call_next(request)
    return response

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
def get_current_user(authorization: Optional[str] = Header(None)):
    """
    Very simple token authentication.
    Expects header: Authorization: Bearer <APP_SECRET>
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )
    token = authorization.split(" ", 1)[1]
    if token != APP_SECRET:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
    return token  # token itself is not used further


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class InviteRequest(BaseModel):
    email: EmailStr = Field(..., example="user@example.com")


class InviteResponse(BaseModel):
    invite_id: str
    message: str


class CreateUserRequest(BaseModel):
    invite_id: str = Field(..., example="c0a80123-4b5d-4f2a-9c1e-2d5f6a7b8c9d")
    user_name: str = Field(..., example="newuser")
    password: str = Field(..., min_length=6, max_length=PASSWORD_MAX_LENGTH)

    @validator("user_name")
    def validate_user_name(cls, v):
        if not v:
            raise ValueError("user_name must not be empty")
        if len(v) > 32:
            raise ValueError("user_name must be at most 32 characters")
        import re
        if not re.match(USERNAME_REGEX, v):
            raise ValueError("user_name contains invalid characters")
        return v


class MessageResponse(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
def generate_invite_id() -> str:
    return str(uuid.uuid4())


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.post(
    "/invite_user",
    response_model=InviteResponse,
    summary="Create an invitation",
    dependencies=[Depends(get_current_user)]
)
def invite_user(
    request: InviteRequest,
    db: sqlite3.Connection = Depends(get_db)
):
    # Cleanup old invitations first
    delete_expired_invitations(db)

    email = request.email.lower()
    cur = db.cursor()
    cur.execute("SELECT invite_id FROM invitations WHERE email = ?", (email,))
    row = cur.fetchone()
    if row:
        invite_id = row["invite_id"]
    else:
        invite_id = generate_invite_id()
        created_at = datetime.datetime.utcnow().isoformat()
        try:
            cur.execute(
                "INSERT INTO invitations (email, invite_id, created_at) VALUES (?, ?, ?)",
                (email, invite_id, created_at),
            )
            db.commit()
        except sqlite3.IntegrityError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Database error while creating invitation."
            ) from e

    # Uniform response to avoid enumeration
    return InviteResponse(
        invite_id=invite_id,
        message="Invitation processed successfully."
    )


@app.post(
    "/create_user",
    response_model=MessageResponse,
    responses={
        400: {"model": MessageResponse},
        401: {"model": MessageResponse},
    },
    summary="Create a user using an invitation"
)
def create_user(
    request: CreateUserRequest,
    db: sqlite3.Connection = Depends(get_db)
):
    cur = db.cursor()
    # Validate invitation
    cur.execute(
        "SELECT email FROM invitations WHERE invite_id = ?", (request.invite_id,)
    )
    invite_row = cur.fetchone()
    if not invite_row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid invite_id"
        )
    email = invite_row["email"]

    # Check if username already exists
    cur.execute(
        "SELECT 1 FROM users WHERE username = ?", (request.user_name,)
    )
    if cur.fetchone():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists. Try providing different username."
        )

    # Hash password and create user
    password_hash = hash_password(request.password)
    created_at = datetime.datetime.utcnow().isoformat()
    try:
        cur.execute(
            "INSERT INTO users (username, password_hash, email, created_at) VALUES (?, ?, ?, ?)",
            (request.user_name, password_hash, email, created_at),
        )
        # Delete the used invitation
        cur.execute(
            "DELETE FROM invitations WHERE invite_id = ?", (request.invite_id,)
        )
        db.commit()
    except sqlite3.IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error while creating user."
        ) from e

    return MessageResponse(message="User created successfully.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)