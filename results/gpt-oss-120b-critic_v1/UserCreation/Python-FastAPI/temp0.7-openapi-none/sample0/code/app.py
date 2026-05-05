import os
import uuid
import sqlite3
from datetime import datetime
from typing import Optional, Dict, Any

import jwt
import uvicorn
from fastapi import (
    FastAPI,
    HTTPException,
    Depends,
    Request,
    status,
    Header,
)
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, EmailStr, Field, ValidationError, validator
from passlib.context import CryptContext
from email_validator import validate_email, EmailNotValidError

# -----------------------------
# Configuration and Constants
# -----------------------------
DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError("APP_SECRET environment variable must be set for security reasons.")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# -----------------------------
# Database Utilities
# -----------------------------
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
        # Invitations table: one per email
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS invitations (
                email TEXT PRIMARY KEY,
                invite_id TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0
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

# Initialize DB when module loads
init_db()

# -----------------------------
# Pydantic Schemas
# -----------------------------
class InviteUserRequest(BaseModel):
    email: EmailStr = Field(..., max_length=254, example="user@example.com")

class InviteUserResponse(BaseModel):
    invite_id: str
    message: str

class CreateUserRequest(BaseModel):
    invite_id: str = Field(..., max_length=64)
    user_name: str = Field(..., max_length=150)
    password: str = Field(..., min_length=8, max_length=128)

    @validator("user_name")
    def strip_username(cls, v: str) -> str:
        return v.strip()

    @validator("invite_id")
    def strip_invite_id(cls, v: str) -> str:
        return v.strip()

class MessageResponse(BaseModel):
    message: str

# -----------------------------
# FastAPI Application
# -----------------------------
app = FastAPI(
    title="InviteAPI",
    version="1.0.11",
    description="Provides invite-only micro-service.",
)

# -----------------------------
# Helper Functions
# -----------------------------
def generate_invite_id() -> str:
    return uuid.uuid4().hex

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

# -----------------------------
# Security Dependencies
# -----------------------------
def get_current_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    """
    Simple JWT based authentication. Expects header:
        Authorization: Bearer <token>
    The token must be signed with APP_SECRET.
    """
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Authorization header")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Authorization header format")
    token = parts[1]
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return payload

# -----------------------------
# Endpoints
# -----------------------------
@app.post(
    "/invite_user",
    response_model=InviteUserResponse,
    dependencies=[Depends(get_current_user)],
)
def invite_user(request: InviteUserRequest, db: sqlite3.Connection = Depends(get_db)):
    email = request.email.strip().lower()
    # Extra validation using email_validator
    try:
        validate_email(email)
    except EmailNotValidError as e:
        raise HTTPException(status_code=400, detail=str(e))

    cur = db.cursor()
    # Check if invitation already exists
    cur.execute("SELECT invite_id FROM invitations WHERE email = ?", (email,))
    row = cur.fetchone()
    if row:
        invite_id = row["invite_id"]
        message = "Existing invitation retrieved."
        return InviteUserResponse(invite_id=invite_id, message=message)

    # Create new invitation, handling race condition
    invite_id = generate_invite_id()
    now = datetime.utcnow().isoformat()
    try:
        cur.execute(
            "INSERT INTO invitations (email, invite_id, created_at, used) VALUES (?, ?, ?, 0)",
            (email, invite_id, now),
        )
        db.commit()
        message = "Invitation created."
    except sqlite3.IntegrityError:
        # Another request inserted the same email concurrently
        cur.execute("SELECT invite_id FROM invitations WHERE email = ?", (email,))
        row = cur.fetchone()
        if row:
            invite_id = row["invite_id"]
            message = "Existing invitation retrieved."
        else:
            raise HTTPException(status_code=500, detail="Failed to create invitation.")
    return InviteUserResponse(invite_id=invite_id, message=message)


@app.post(
    "/create_user",
    response_model=MessageResponse,
    responses={
        400: {"model": MessageResponse},
        401: {"model": MessageResponse},
    },
)
def create_user(payload: CreateUserRequest, db: sqlite3.Connection = Depends(get_db)):
    invite_id = payload.invite_id
    user_name = payload.user_name
    password = payload.password

    cur = db.cursor()
    # Verify invitation exists and not used
    cur.execute(
        "SELECT email, used FROM invitations WHERE invite_id = ?",
        (invite_id,),
    )
    inv = cur.fetchone()
    if not inv:
        raise HTTPException(status_code=401, detail="Invalid invite_id")
    if inv["used"]:
        raise HTTPException(status_code=401, detail="Invite already used")

    # Check username uniqueness (optimistic)
    cur.execute("SELECT id FROM users WHERE user_name = ?", (user_name,))
    if cur.fetchone():
        raise HTTPException(
            status_code=400,
            detail="Username already exists. Try providing different username.",
        )

    # Create user
    password_hash = hash_password(password)
    now = datetime.utcnow().isoformat()
    try:
        cur.execute(
            "INSERT INTO users (user_name, password_hash, created_at) VALUES (?, ?, ?)",
            (user_name, password_hash, now),
        )
        # Mark invitation as used
        cur.execute(
            "UPDATE invitations SET used = 1 WHERE invite_id = ?",
            (invite_id,),
        )
        db.commit()
    except sqlite3.IntegrityError:
        # Race condition on username uniqueness
        raise HTTPException(
            status_code=400,
            detail="Username already exists. Try providing different username.",
        )

    return MessageResponse(message="User created successfully.")

# -----------------------------
# Custom Exception Handlers
# -----------------------------
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # Return JSON with 'message' field as required by schema
    return JSONResponse(
        status_code=exc.status_code,
        content={"message": exc.detail},
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Combine all error messages into a single string
    errors = "; ".join([e["msg"] for e in exc.errors()])
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"message": errors},
    )

# -----------------------------
# Run Server
# -----------------------------
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)