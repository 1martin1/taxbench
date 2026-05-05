import os
import uuid
import sqlite3
import datetime
import time
from typing import Optional, Dict, Tuple, List

from fastapi import FastAPI, Depends, Request, status
from fastapi.responses import JSONResponse
from fastapi.exception_handlers import http_exception_handler
from pydantic import BaseModel, EmailStr, Field
from passlib.context import CryptContext
from email_validator import validate_email, EmailNotValidError
from jose import JWTError, jwt

# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------
APP_SECRET = os.getenv("APP_SECRET", "defaultsecret")
DB_PATH = "db.sqlite3"

# ------------------------------------------------------------
# Database utilities
# ------------------------------------------------------------
def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    conn = get_connection()
    cur = conn.cursor()
    # invitations table with expiration (7 days)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS invitations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            invite_id TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP DEFAULT (datetime('now', '+7 days'))
        )
        """
    )
    # users table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_name TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()

# ------------------------------------------------------------
# Password hashing
# ------------------------------------------------------------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

# ------------------------------------------------------------
# Pydantic models
# ------------------------------------------------------------
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

# ------------------------------------------------------------
# FastAPI app
# ------------------------------------------------------------
app = FastAPI(title="InviteAPI", version="1.0.11")

@app.on_event("startup")
def on_startup():
    init_db()

# ------------------------------------------------------------
# Dependency: DB connection
# ------------------------------------------------------------
def get_db():
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()

# ------------------------------------------------------------
# Custom API exception to enforce {"message": "..."} payload
# ------------------------------------------------------------
class APIException(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message

@app.exception_handler(APIException)
async def api_exception_handler(request: Request, exc: APIException):
    return JSONResponse(status_code=exc.status_code, content={"message": exc.message})

# ------------------------------------------------------------
# Authentication
# ------------------------------------------------------------
def get_current_user(request: Request) -> Dict:
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        raise APIException(status_code=401, message="Missing authentication token")
    token = auth.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=["HS256"])
        return payload
    except JWTError:
        raise APIException(status_code=401, message="Invalid authentication token")

# ------------------------------------------------------------
# Simple in‑memory rate limiter
# ------------------------------------------------------------
_RATE_LIMIT: Dict[Tuple[str, str], List[float]] = {}
_RATE_LIMIT_MAX = 10          # max requests
_RATE_LIMIT_WINDOW = 60       # seconds

def rate_limiter(user: Dict = Depends(get_current_user), request: Request = None):
    key = (user.get("sub", "anonymous"), request.url.path if request else "unknown")
    now = time.time()
    timestamps = _RATE_LIMIT.get(key, [])
    # keep only timestamps within the window
    timestamps = [t for t in timestamps if now - t < _RATE_LIMIT_WINDOW]
    if len(timestamps) >= _RATE_LIMIT_MAX:
        raise APIException(status_code=429, message="Rate limit exceeded")
    timestamps.append(now)
    _RATE_LIMIT[key] = timestamps

# ------------------------------------------------------------
# Helper functions for invitations and users
# ------------------------------------------------------------
def generate_invite_id() -> str:
    return uuid.uuid4().hex

def fetch_invitation_by_email(conn: sqlite3.Connection, email: str) -> Optional[sqlite3.Row]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM invitations WHERE email = ?", (email,))
    row = cur.fetchone()
    if row and _invitation_expired(row):
        return None
    return row

def fetch_invitation_by_id(conn: sqlite3.Connection, invite_id: str) -> Optional[sqlite3.Row]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM invitations WHERE invite_id = ?", (invite_id,))
    row = cur.fetchone()
    if row and _invitation_expired(row):
        return None
    return row

def _invitation_expired(row: sqlite3.Row) -> bool:
    expires_at = row["expires_at"]
    if not expires_at:
        return False
    try:
        exp_dt = datetime.datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        # SQLite may return with fractional seconds; fallback
        exp_dt = datetime.datetime.fromisoformat(expires_at)
    return exp_dt < datetime.datetime.utcnow()

def create_invitation(conn: sqlite3.Connection, email: str, invite_id: str):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO invitations (email, invite_id) VALUES (?, ?)",
        (email, invite_id)
    )
    conn.commit()

def fetch_user_by_name(conn: sqlite3.Connection, user_name: str) -> Optional[sqlite3.Row]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_name = ?", (user_name,))
    return cur.fetchone()

def create_user_record(conn: sqlite3.Connection, user_name: str, password_hash: str):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (user_name, password_hash) VALUES (?, ?)",
        (user_name, password_hash)
    )
    conn.commit()

def delete_invitation(conn: sqlite3.Connection, invite_id: str):
    cur = conn.cursor()
    cur.execute("DELETE FROM invitations WHERE invite_id = ?", (invite_id,))
    conn.commit()

# ------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------
@app.post(
    "/invite_user",
    response_model=InviteResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limiter), Depends(get_current_user)]
)
def invite_user(
    request: InviteRequest,
    db: sqlite3.Connection = Depends(get_db)
):
    # Validate email format (pydantic already did it, double‑check)
    try:
        validate_email(request.email)
    except EmailNotValidError as exc:
        raise APIException(status_code=400, message=str(exc))

    existing = fetch_invitation_by_email(db, request.email)
    if existing:
        invite_id = existing["invite_id"]
        message = "Existing invitation retrieved."
    else:
        invite_id = generate_invite_id()
        create_invitation(db, request.email, invite_id)
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
    dependencies=[Depends(rate_limiter), Depends(get_current_user)]
)
def create_user_endpoint(
    payload: CreateUserRequest,
    db: sqlite3.Connection = Depends(get_db)
):
    invitation = fetch_invitation_by_id(db, payload.invite_id)
    if not invitation:
        raise APIException(status_code=401, message="Invalid invite_id")

    if fetch_user_by_name(db, payload.user_name):
        raise APIException(
            status_code=400,
            message="Username already exists. Try providing different username."
        )

    password_hash = hash_password(payload.password)
    create_user_record(db, payload.user_name, password_hash)

    # Invalidate invitation after successful use
    delete_invitation(db, payload.invite_id)

    return MessageResponse(message="User created successfully.")

# ------------------------------------------------------------
# Run server
# ------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)