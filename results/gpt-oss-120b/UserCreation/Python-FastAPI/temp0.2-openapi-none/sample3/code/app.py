import os
import uuid
import sqlite3
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, validator
from passlib.context import CryptContext
from email_validator import validate_email, EmailNotValidError

# Initialize FastAPI app
app = FastAPI(title="InviteAPI", version="1.0.11")

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Database path
DB_PATH = "db.sqlite3"

# -------------------------------------------------
# Database utilities
# -------------------------------------------------
def get_db():
    """Provide a thread‑safe SQLite connection."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    # Invitations table: one invitation per email
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS invitations (
            email TEXT PRIMARY KEY,
            invite_id TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    # Users table
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
    conn.close()

@app.on_event("startup")
def on_startup():
    init_db()

# -------------------------------------------------
# Pydantic models
# -------------------------------------------------
class InviteUserRequest(BaseModel):
    email: EmailStr

class InviteUserResponse(BaseModel):
    invite_id: str
    message: str = Field(default="Invitation generated")

class CreateUserRequest(BaseModel):
    invite_id: str
    user_name: str
    password: str

    @validator("user_name")
    def name_not_empty(cls, v):
        if not v.strip():
            raise ValueError("user_name cannot be empty")
        return v

    @validator("password")
    def password_not_empty(cls, v):
        if not v:
            raise ValueError("password cannot be empty")
        return v

class MessageResponse(BaseModel):
    message: str

# -------------------------------------------------
# Helper functions
# -------------------------------------------------
def generate_invite_id() -> str:
    return uuid.uuid4().hex

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_email(email: str) -> str:
    """Validate email using email_validator, return normalized email."""
    try:
        v = validate_email(email)
        return v.email
    except EmailNotValidError as e:
        raise HTTPException(status_code=400, detail=str(e))

# -------------------------------------------------
# Endpoints
# -------------------------------------------------
@app.post("/invite_user", response_model=InviteUserResponse)
def invite_user(payload: InviteUserRequest):
    email = verify_email(payload.email)

    conn = get_db()
    cur = conn.cursor()
    # Check if invitation already exists
    cur.execute(
        "SELECT invite_id FROM invitations WHERE email = ?",
        (email,)
    )
    row = cur.fetchone()
    if row:
        invite_id = row["invite_id"]
        conn.close()
        return InviteUserResponse(invite_id=invite_id, message="Invitation already exists")
    # Create new invitation
    invite_id = generate_invite_id()
    created_at = datetime.utcnow().isoformat()
    try:
        cur.execute(
            "INSERT INTO invitations (email, invite_id, created_at) VALUES (?, ?, ?)",
            (email, invite_id, created_at)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=500, detail="Database integrity error")
    conn.close()
    return InviteUserResponse(invite_id=invite_id, message="Invitation generated")

@app.post("/create_user", response_model=MessageResponse, responses={
    400: {"model": MessageResponse},
    401: {"model": MessageResponse}
})
def create_user(payload: CreateUserRequest):
    conn = get_db()
    cur = conn.cursor()
    # Verify invitation
    cur.execute(
        "SELECT email FROM invitations WHERE invite_id = ?",
        (payload.invite_id,)
    )
    invite = cur.fetchone()
    if not invite:
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid invite_id")
    # Check username uniqueness
    cur.execute(
        "SELECT 1 FROM users WHERE user_name = ?",
        (payload.user_name,)
    )
    if cur.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Username already exists. Try providing different username.")
    # Hash password and create user
    password_hash = hash_password(payload.password)
    created_at = datetime.utcnow().isoformat()
    try:
        cur.execute(
            "INSERT INTO users (user_name, password_hash, created_at) VALUES (?, ?, ?)",
            (payload.user_name, password_hash, created_at)
        )
        # Optionally delete the invitation so it cannot be reused
        cur.execute(
            "DELETE FROM invitations WHERE invite_id = ?",
            (payload.invite_id,)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=500, detail="Database integrity error")
    conn.close()
    return MessageResponse(message="User created")

# -------------------------------------------------
# Run the application
# -------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    # Host and port as required
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)