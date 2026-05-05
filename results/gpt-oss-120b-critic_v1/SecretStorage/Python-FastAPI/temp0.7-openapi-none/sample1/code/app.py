import os
import secrets
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, status, Request, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field
from passlib.context import CryptContext
from jose import JWTError, jwt
from dotenv import load_dotenv
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

# ----------------------------------------------------------------------
# Configuration & Logging
# ----------------------------------------------------------------------
load_dotenv()
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    # Generate a secure random secret if not provided
    APP_SECRET = secrets.token_urlsafe(32)
    logging.warning("APP_SECRET not set; using a generated secret for this session.")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
MAX_REQUEST_BODY_SIZE = 1 * 1024 * 1024  # 1 MB
MAX_SECRET_LENGTH = 1024  # characters

# ----------------------------------------------------------------------
# FastAPI app
# ----------------------------------------------------------------------
app = FastAPI(title="Secret Storage", version="1.0.0")

# ----------------------------------------------------------------------
# Security utilities
# ----------------------------------------------------------------------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, APP_SECRET, algorithm=ALGORITHM)


# ----------------------------------------------------------------------
# Database helpers
# ----------------------------------------------------------------------
DB_PATH = "db.sqlite3"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS secrets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                secret TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()


init_db()


def get_user_by_email(conn: sqlite3.Connection, email: str):
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE email = ?", (email,))
    return cur.fetchone()


def get_user_by_username(conn: sqlite3.Connection, username: str):
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = ?", (username,))
    return cur.fetchone()


def get_user_by_id(conn: sqlite3.Connection, user_id: int):
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    return cur.fetchone()


# ----------------------------------------------------------------------
# Authentication dependency
# ----------------------------------------------------------------------
def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
):
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )
    token = credentials.credentials
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=[ALGORITHM])
        user_id: int = payload.get("sub")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token",
            )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )
    with get_db() as conn:
        user = get_user_by_id(conn, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )
    return dict(user)


# ----------------------------------------------------------------------
# Request size limiting middleware
# ----------------------------------------------------------------------
class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_body_size: int):
        super().__init__(app)
        self.max_body_size = max_body_size

    async def dispatch(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            body = await request.body()
            if len(body) > self.max_body_size:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large"},
                )
            # Re-inject the body for downstream handlers
            async def receive():
                return {"type": "http.request", "body": body, "more_body": False}
            request._receive = receive
        response = await call_next(request)
        return response


app.add_middleware(MaxBodySizeMiddleware, max_body_size=MAX_REQUEST_BODY_SIZE)


# ----------------------------------------------------------------------
# Pydantic models
# ----------------------------------------------------------------------
class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=1, max_length=150)
    password: str = Field(..., min_length=8)


class RegisterResponse(BaseModel):
    message: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)


class LoginResponse(BaseModel):
    token: str
    message: str


class SetSecretRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=150)
    secret: str = Field(..., max_length=MAX_SECRET_LENGTH)


class SetSecretResponse(BaseModel):
    message: str


class GetSecretResponse(BaseModel):
    secret: str


class MessageResponse(BaseModel):
    message: str


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------
@app.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    responses={400: {"model": MessageResponse}},
)
def register(payload: RegisterRequest):
    with get_db() as conn:
        # Check for existing email or username without revealing which one
        if get_user_by_email(conn, payload.email) or get_user_by_username(conn, payload.username):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Registration failed",
            )
        password_hash = get_password_hash(payload.password)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (email, username, password_hash) VALUES (?, ?, ?)",
            (payload.email, payload.username, password_hash),
        )
        conn.commit()
    return RegisterResponse(message="Registration successful")


@app.post(
    "/login",
    response_model=LoginResponse,
    responses={401: {"model": MessageResponse}},
)
def login(payload: LoginRequest):
    with get_db() as conn:
        user = get_user_by_email(conn, payload.email)
        if not user or not verify_password(payload.password, user["password_hash"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )
        token_data = {"sub": user["id"], "username": user["username"]}
        token = create_access_token(token_data)
    return LoginResponse(token=token, message="Login successful")


@app.post(
    "/set_secret",
    response_model=SetSecretResponse,
    responses={401: {"model": MessageResponse}},
)
def set_secret(
    payload: SetSecretRequest,
    current_user: dict = Depends(get_current_user),
):
    # Ensure token user matches the username in payload
    if current_user["username"] != payload.username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )
    with get_db() as conn:
        cur = conn.cursor()
        # Upsert secret: delete existing then insert
        cur.execute("DELETE FROM secrets WHERE user_id = ?", (current_user["id"],))
        cur.execute(
            "INSERT INTO secrets (user_id, secret) VALUES (?, ?)",
            (current_user["id"], payload.secret),
        )
        conn.commit()
    return SetSecretResponse(message="Secret has been set successfully")


@app.get(
    "/get_secret",
    response_model=GetSecretResponse,
    responses={401: {"model": MessageResponse}, 404: {"model": MessageResponse}},
)
def get_secret(
    username: str = Query(..., min_length=1, max_length=150),
    current_user: dict = Depends(get_current_user),
):
    # Token must belong to requested username
    if current_user["username"] != username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT secret FROM secrets
            WHERE user_id = (SELECT id FROM users WHERE username = ?)
            """,
            (username,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Secret not found",
            )
        secret = row["secret"]
    return GetSecretResponse(secret=secret)


# ----------------------------------------------------------------------
# Run server
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)