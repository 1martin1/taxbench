import os
import re
import hmac
import sqlite3
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Depends, status, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt, JWTError
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field, ConfigDict

DB_PATH = "db.sqlite3"
APP_SECRET = os.environ.get("APP_SECRET", "change-me-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)

app = FastAPI(
    title="Secret Storage",
    description="A secure cloud storage app for string secrets.",
    version="1.0.0",
)


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS secrets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                secret TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)"
        )


@app.on_event("startup")
def startup_event() -> None:
    init_db()


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    username: str = Field(min_length=3, max_length=50, examples=["user1"])
    password: str = Field(min_length=8, max_length=256)

    @staticmethod
    def validate_username_value(username: str) -> bool:
        return bool(re.fullmatch(r"[A-Za-z0-9_]+", username))


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class SetSecretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=50)
    secret: str = Field(min_length=1, max_length=100000)


class MessageResponse(BaseModel):
    message: str


class LoginResponse(BaseModel):
    token: str
    message: str


class SecretResponse(BaseModel):
    secret: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return pwd_context.verify(password, password_hash)
    except Exception:
        return False


def create_token(user_id: int, email: str, username: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "username": username,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=JWT_EXPIRE_HOURS)).timestamp()),
    }
    return jwt.encode(payload, APP_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, APP_SECRET, algorithms=[JWT_ALGORITHM])


def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)) -> sqlite3.Row:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    token = credentials.credentials
    try:
        payload = decode_token(token)
        user_id = int(payload.get("sub", "0"))
    except (JWTError, ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    with get_db() as conn:
        user = conn.execute(
            "SELECT id, email, username, password_hash, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    token_username = payload.get("username")
    token_email = payload.get("email")
    if not hmac.compare_digest(str(user["username"]), str(token_username or "")) or not hmac.compare_digest(
        str(user["email"]), str(token_email or "")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    return user


@app.post(
    "/register",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Authentication"],
)
def register(request: RegisterRequest):
    if not RegisterRequest.validate_username_value(request.username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )

    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE email = ? OR username = ?",
            (request.email.lower(), request.username),
        ).fetchone()
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already in use or invalid data",
            )

        password_hash = hash_password(request.password)
        conn.execute(
            """
            INSERT INTO users (email, username, password_hash, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (request.email.lower(), request.username, password_hash, utc_now_iso()),
        )

    return {"message": "Registration successful"}


@app.post("/login", response_model=LoginResponse, tags=["Authentication"], security=[])
def login(request: LoginRequest):
    with get_db() as conn:
        user = conn.execute(
            "SELECT id, email, username, password_hash FROM users WHERE email = ?",
            (request.email.lower(),),
        ).fetchone()

    if user is None or not verify_password(request.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = create_token(user["id"], user["email"], user["username"])
    return {"token": token, "message": "Login successful"}


@app.post("/set_secret", response_model=MessageResponse)
def set_secret(request: SetSecretRequest, current_user: sqlite3.Row = Depends(get_current_user)):
    if not hmac.compare_digest(current_user["username"], request.username):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM secrets WHERE user_id = ?",
            (current_user["id"],),
        ).fetchone()

        if existing is None:
            conn.execute(
                """
                INSERT INTO secrets (user_id, secret, updated_at)
                VALUES (?, ?, ?)
                """,
                (current_user["id"], request.secret, utc_now_iso()),
            )
        else:
            conn.execute(
                """
                UPDATE secrets
                SET secret = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (request.secret, utc_now_iso(), current_user["id"]),
            )

    return {"message": "Secret has been set successfully"}


@app.get("/get_secret", response_model=SecretResponse)
def get_secret(
    username: str = Query(..., min_length=3, max_length=50),
    current_user: sqlite3.Row = Depends(get_current_user),
):
    if not hmac.compare_digest(current_user["username"], username):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    with get_db() as conn:
        row = conn.execute(
            """
            SELECT s.secret
            FROM secrets s
            INNER JOIN users u ON u.id = s.user_id
            WHERE u.username = ?
            """,
            (username,),
        ).fetchone()

    return {"secret": row["secret"] if row is not None else ""}


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException):
    if exc.status_code in (400, 401):
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return fastapi_json_response(exc.status_code, {"message": detail})
    return fastapi_json_response(exc.status_code, {"detail": exc.detail})


def fastapi_json_response(status_code: int, content: dict):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status_code, content=content)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)