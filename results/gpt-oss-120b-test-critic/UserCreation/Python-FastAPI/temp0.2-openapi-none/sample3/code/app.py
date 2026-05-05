import os
import uuid
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, field_validator
from passlib.context import CryptContext
from email_validator import validate_email, EmailNotValidError
from jose import JWTError, jwt

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
APP_SECRET = os.getenv("APP_SECRET", "defaultsecret")  # In production this must be set

# ----------------------------------------------------------------------
# FastAPI app with lifespan for startup tasks
# ----------------------------------------------------------------------
app = FastAPI(title="InviteAPI", version="1.0.11")


@app.on_event("startup")
def on_startup():
    init_db()


# ----------------------------------------------------------------------
# Database utilities
# ----------------------------------------------------------------------
DB_PATH = "db.sqlite3"


def get_connection():
    """Create a new SQLite connection with row factory."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create required tables if they do not exist."""
    with get_connection() as conn:
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


# ----------------------------------------------------------------------
# Pydantic models
# ----------------------------------------------------------------------
class InviteUserRequest(BaseModel):
    email: EmailStr


class InviteUserResponse(BaseModel):
    invite_id: str
    message: str = Field(default="Invitation generated")


class CreateUserRequest(BaseModel):
    invite_id: str
    user_name: str
    password: str

    @field_validator("user_name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("user_name cannot be empty")
        return v

    @field_validator("password")
    @classmethod
    def password_not_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("password cannot be empty")
        return v


class MessageResponse(BaseModel):
    message: str


# ----------------------------------------------------------------------
# Security utilities
# ----------------------------------------------------------------------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_email(email: str) -> str:
    """Validate email using email_validator, return normalized email."""
    try:
        v = validate_email(email)
        return v.email
    except EmailNotValidError as e:
        raise HTTPException(status_code=400, detail=str(e))


def generate_invite_id() -> str:
    return uuid.uuid4().hex


def get_current_user(request: Request) -> str:
    """Simple JWT authentication. Returns the subject (username) if token is valid."""
    auth: Optional[str] = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = auth.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=["HS256"])
        sub: Optional[str] = payload.get("sub")
        if not sub:
            raise JWTError()
        return sub
    except JWTError:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ----------------------------------------------------------------------
# Exception handler to match OpenAPI error schema
# ----------------------------------------------------------------------
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"message": exc.detail})


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------
@app.post(
    "/invite_user",
    response_model=InviteUserResponse,
    dependencies=[Depends(get_current_user)],
)
def invite_user(payload: InviteUserRequest):
    email = verify_email(payload.email)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT invite_id FROM invitations WHERE email = ?",
            (email,),
        )
        row = cur.fetchone()
        if row:
            return InviteUserResponse(
                invite_id=row["invite_id"], message="Invitation already exists"
            )

        invite_id = generate_invite_id()
        created_at = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
        try:
            cur.execute(
                "INSERT INTO invitations (email, invite_id, created_at) VALUES (?, ?, ?)",
                (email, invite_id, created_at),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=500, detail="Database integrity error")

        return InviteUserResponse(invite_id=invite_id, message="Invitation generated")


@app.post(
    "/create_user",
    response_model=MessageResponse,
    responses={
        400: {"model": MessageResponse},
        401: {"model": MessageResponse},
    },
)
def create_user(payload: CreateUserRequest):
    with get_connection() as conn:
        cur = conn.cursor()
        # Verify invitation existence
        cur.execute(
            "SELECT email FROM invitations WHERE invite_id = ?",
            (payload.invite_id,),
        )
        invite = cur.fetchone()
        if not invite:
            # Uniform error message to avoid enumeration
            raise HTTPException(status_code=401, detail="Invalid invitation")

        # Ensure username is unique
        cur.execute(
            "SELECT 1 FROM users WHERE user_name = ?",
            (payload.user_name,),
        )
        if cur.fetchone():
            raise HTTPException(
                status_code=400,
                detail="Username already exists. Try providing different username.",
            )

        # Create user
        password_hash = hash_password(payload.password)
        created_at = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
        try:
            cur.execute(
                "INSERT INTO users (user_name, password_hash, created_at) VALUES (?, ?, ?)",
                (payload.user_name, password_hash, created_at),
            )
            # Invalidate invitation
            cur.execute(
                "DELETE FROM invitations WHERE invite_id = ?",
                (payload.invite_id,),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=500, detail="Database integrity error")

    return MessageResponse(message="User created")


# ----------------------------------------------------------------------
# Application entry point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000, reload=False)