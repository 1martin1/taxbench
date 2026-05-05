import os
import sqlite3
import uuid
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, ValidationError
from passlib.context import CryptContext
from email_validator import validate_email, EmailNotValidError

# Initialize FastAPI app
app = FastAPI(title="InviteAPI", version="1.0.11")

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Database file
DB_PATH = "db.sqlite3"

# ---------------------------------------------------------------------------
# Database utilities
# ---------------------------------------------------------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        # Table for invitations
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS invitations (
                email TEXT NOT NULL,
                invite_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                PRIMARY KEY (email)
            )
            """
        )
        # Table for users
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_name TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()

@app.on_event("startup")
def on_startup():
    init_db()

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class InviteUserRequest(BaseModel):
    email: EmailStr = Field(..., example="user@example.com")

class InviteUserResponse(BaseModel):
    invite_id: str
    message: str

class CreateUserRequest(BaseModel):
    invite_id: str
    user_name: str
    password: str

class MessageResponse(BaseModel):
    message: str

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
def generate_invite_id() -> str:
    """Generate a short unique invite identifier."""
    return uuid.uuid4().hex[:12]

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.post("/invite_user", response_model=InviteUserResponse)
def invite_user(payload: InviteUserRequest, db: sqlite3.Connection = Depends(get_db)):
    # Validate email (pydantic already does, but double‑check for safety)
    try:
        validate_email(payload.email)
    except EmailNotValidError as e:
        raise HTTPException(status_code=400, detail=str(e))

    cur = db.cursor()
    # Check if invitation already exists for this email
    cur.execute("SELECT invite_id FROM invitations WHERE email = ?", (payload.email,))
    row = cur.fetchone()
    if row:
        invite_id = row["invite_id"]
        message = "Existing invitation retrieved."
    else:
        invite_id = generate_invite_id()
        created_at = datetime.utcnow().isoformat()
        cur.execute(
            "INSERT INTO invitations (email, invite_id, created_at) VALUES (?, ?, ?)",
            (payload.email, invite_id, created_at),
        )
        db.commit()
        message = "Invitation created."

    return InviteUserResponse(invite_id=invite_id, message=message)


@app.post("/create_user", response_model=MessageResponse)
def create_user(payload: CreateUserRequest, db: sqlite3.Connection = Depends(get_db)):
    cur = db.cursor()
    # Verify invitation exists
    cur.execute("SELECT email FROM invitations WHERE invite_id = ?", (payload.invite_id,))
    invite_row = cur.fetchone()
    if not invite_row:
        raise HTTPException(status_code=401, detail="Invalid invite_id")

    # Check username uniqueness
    cur.execute("SELECT id FROM users WHERE user_name = ?", (payload.user_name,))
    if cur.fetchone():
        raise HTTPException(
            status_code=400,
            detail="Username already exists. Try providing different username."
        )

    # Create user
    password_hash = hash_password(payload.password)
    created_at = datetime.utcnow().isoformat()
    cur.execute(
        "INSERT INTO users (user_name, password_hash, created_at) VALUES (?, ?, ?)",
        (payload.user_name, password_hash, created_at)
    )
    # Optionally delete the invitation so it cannot be reused
    cur.execute("DELETE FROM invitations WHERE invite_id = ?", (payload.invite_id,))
    db.commit()

    return MessageResponse(message="User created successfully.")

# ---------------------------------------------------------------------------
# Custom exception handler to match OpenAPI schema (message field)
# ---------------------------------------------------------------------------
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"message": exc.detail}
    )