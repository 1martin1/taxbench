import os
import uuid
import sqlite3
from datetime import datetime
from typing import Generator

from fastapi import FastAPI, HTTPException, Depends, status, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from passlib.context import CryptContext
from contextlib import asynccontextmanager

# ---------- Database Setup ----------
DB_PATH = "db.sqlite3"


def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
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
        # Invitations table
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS invitations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                invite_id TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


# ---------- Security ----------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ---------- Pydantic Models ----------
class InviteRequest(BaseModel):
    email: EmailStr = Field(..., example="user@example.com")


class InviteResponse(BaseModel):
    invite_id: str
    message: str


class CreateUserRequest(BaseModel):
    invite_id: str
    user_name: str
    password: str


class MessageResponse(BaseModel):
    message: str


# ---------- Application ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="InviteAPI", version="1.0.11", lifespan=lifespan)


# ---------- Exception Handlers ----------
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """
    Convert HTTPException detail to a JSON body with a `message` field,
    matching the OpenAPI contract.
    """
    return JSONResponse(status_code=exc.status_code, content={"message": exc.detail})


# ---------- Endpoints ----------
@app.post("/invite_user", response_model=InviteResponse)
def invite_user(request: InviteRequest, db: sqlite3.Connection = Depends(get_db)):
    cur = db.cursor()
    # Check if invitation already exists for this email
    cur.execute("SELECT invite_id FROM invitations WHERE email = ?", (request.email,))
    row = cur.fetchone()
    if row:
        invite_id = row["invite_id"]
        message = "Existing invitation retrieved."
    else:
        # Create new invitation
        invite_id = uuid.uuid4().hex
        created_at = datetime.utcnow().isoformat()
        cur.execute(
            "INSERT INTO invitations (email, invite_id, created_at) VALUES (?, ?, ?)",
            (request.email, invite_id, created_at),
        )
        db.commit()
        message = "Invitation created."
    return InviteResponse(invite_id=invite_id, message=message)


@app.post("/create_user", response_model=MessageResponse)
def create_user(request: CreateUserRequest, db: sqlite3.Connection = Depends(get_db)):
    cur = db.cursor()
    # Validate invitation
    cur.execute("SELECT email FROM invitations WHERE invite_id = ?", (request.invite_id,))
    invite_row = cur.fetchone()
    if not invite_row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid invite_id",
        )
    # Check username uniqueness
    cur.execute("SELECT id FROM users WHERE user_name = ?", (request.user_name,))
    if cur.fetchone():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists. Try providing different username.",
        )
    # Create user
    password_hash = hash_password(request.password)
    created_at = datetime.utcnow().isoformat()
    cur.execute(
        "INSERT INTO users (user_name, password_hash, created_at) VALUES (?, ?, ?)",
        (request.user_name, password_hash, created_at),
    )
    # Delete used invitation to prevent reuse
    cur.execute("DELETE FROM invitations WHERE invite_id = ?", (request.invite_id,))
    db.commit()
    return MessageResponse(message="User created successfully.")


# ---------- Run Server ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000, reload=False)