import os
import re
import hmac
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from typing import Optional, Dict, List

import uvicorn
from fastapi import FastAPI, HTTPException, Depends, status, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt, JWTError
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field, ConfigDict

DB_PATH = "db.sqlite3"
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24

_raw_secret = os.environ.get("APP_SECRET")
if not _raw_secret or len(_raw_secret) < 32:
    raise RuntimeError("APP_SECRET must be set and at least 32 characters long")
APP_SECRET = _raw_secret

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)

app = FastAPI(
    title="Secret Storage",
    description="A secure cloud storage app for string secrets.",
    version="1.0.0",
)

USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_]+")
LOGIN_WINDOW_SECONDS = 900
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 900

_login_attempts_lock = threading.Lock()
_login_attempts: Dict[str, List[datetime]] = {}
_login_lockouts: Dict[str, datetime] = {}


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
        return bool(USERNAME_PATTERN.fullmatch(username))


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


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def fastapi_json_response(status_code: int, content: dict):
    return JSONResponse(status_code=status_code, content=content)


def is_valid_username(username: str) -> bool:
    return 3 <= len(username) <= 50 and bool(USERNAME_PATTERN.fullmatch(username))


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return pwd_context.verify(password, password_hash)
    except Exception:
        return False


def create_token(user_id: int, email: str, username: str) -> str:
    now = utc_now()
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


def _cleanup_login_tracking(now: datetime) -> None:
    window_start = now - timedelta(seconds=LOGIN_WINDOW_SECONDS)
    expired_lockouts = [key for key, until in _login_lockouts.items() if until <= now]
    for key in expired_lockouts:
        _login_lockouts.pop(key, None)

    expired_attempt_keys = []
    for key, attempts in _login_attempts.items():
        filtered = [ts for ts in attempts if ts > window_start]
        if filtered:
            _login_attempts[key] = filtered
        else:
            expired_attempt_keys.append(key)

    for key in expired_attempt_keys:
        _login_attempts.pop(key, None)


def _is_login_locked(identifier: str) -> bool:
    now = utc_now()
    with _login_attempts_lock:
        _cleanup_login_tracking(now)
        locked_until = _login_lockouts.get(identifier)
        return locked_until is not None and locked_until > now


def _record_login_failure(identifier: str) -> None:
    now = utc_now()
    with _login_attempts_lock:
        _cleanup_login_tracking(now)
        attempts = _login_attempts.get(identifier, [])
        attempts.append(now)
        _login_attempts[identifier] = attempts
        if len(attempts) >= LOGIN_MAX_ATTEMPTS:
            _login_lockouts[identifier] = now + timedelta(seconds=LOGIN_LOCKOUT_SECONDS)
            _login_attempts.pop(identifier, None)


def _clear_login_failures(identifier: str) -> None:
    with _login_attempts_lock:
        _login_attempts.pop(identifier, None)
        _login_lockouts.pop(identifier, None)


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


@app.post("/login", response_model=LoginResponse, tags=["Authentication"])
def login(request: LoginRequest):
    identifier = request.email.lower()

    if _is_login_locked(identifier):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    with get_db() as conn:
        user = conn.execute(
            "SELECT id, email, username, password_hash FROM users WHERE email = ?",
            (identifier,),
        ).fetchone()

    if user is None or not verify_password(request.password, user["password_hash"]):
        _record_login_failure(identifier)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    _clear_login_failures(identifier)
    token = create_token(user["id"], user["email"], user["username"])
    return {"token": token, "message": "Login successful"}


@app.post("/set_secret", response_model=MessageResponse)
def set_secret(request: SetSecretRequest, current_user: sqlite3.Row = Depends(get_current_user)):
    if not is_valid_username(request.username):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

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
    if not is_valid_username(username):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

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


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    if request.url.path == "/register":
        return fastapi_json_response(
            status.HTTP_400_BAD_REQUEST,
            {"message": "Email already in use or invalid data"},
        )
    if request.url.path == "/login":
        return fastapi_json_response(
            status.HTTP_401_UNAUTHORIZED,
            {"message": "Invalid email or password"},
        )
    return fastapi_json_response(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        {"detail": exc.errors()},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException):
    if exc.status_code in (400, 401):
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return fastapi_json_response(exc.status_code, {"message": detail})
    return fastapi_json_response(exc.status_code, {"detail": exc.detail})


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)