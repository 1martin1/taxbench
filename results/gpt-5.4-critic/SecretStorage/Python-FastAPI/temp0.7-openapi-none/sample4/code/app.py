import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import uvicorn
from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel, ConfigDict, Field, field_validator


DB_PATH = "db.sqlite3"
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_MINUTES = 15
PBKDF2_ITERATIONS = 600_000
MAX_SECRET_LENGTH = 10000

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,50}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

bearer_scheme = HTTPBearer(auto_error=False)

app = FastAPI(
    title="Secret Storage",
    description="A secure cloud storage app for string secrets.",
    version="1.0.0",
)


def get_app_secret() -> str:
    secret = os.getenv("APP_SECRET")
    if not secret or len(secret) < 32:
        raise RuntimeError("APP_SECRET must be set and at least 32 characters long")
    return secret


APP_SECRET = get_app_secret()


class SlidingWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.time()
        cutoff = now - self.window_seconds
        with self._lock:
            events = self._events.get(key, [])
            events = [ts for ts in events if ts > cutoff]
            if len(events) >= self.limit:
                self._events[key] = events
                return False
            events.append(now)
            self._events[key] = events
            return True


auth_rate_limiter = SlidingWindowRateLimiter(limit=5, window_seconds=60)
secret_write_rate_limiter = SlidingWindowRateLimiter(limit=20, window_seconds=60)


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
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

    email: str
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=256)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) > 254 or not EMAIL_PATTERN.fullmatch(normalized):
            raise ValueError("invalid email")
        return normalized


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    password: str = Field(min_length=1, max_length=256)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) > 254 or not EMAIL_PATTERN.fullmatch(normalized):
            raise ValueError("invalid email")
        return normalized


class SetSecretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=50)
    secret: str = Field(min_length=1, max_length=MAX_SECRET_LENGTH)


class MessageResponse(BaseModel):
    message: str


class LoginResponse(BaseModel):
    token: str
    message: str


class SecretResponse(BaseModel):
    secret: str


def json_message(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"message": message})


@app.exception_handler(JWTError)
def jwt_error_handler(request: Request, exc: JWTError):
    return json_message(status.HTTP_401_UNAUTHORIZED, "Invalid authentication token")


@app.exception_handler(Exception)
def validation_passthrough_handler(request: Request, exc: Exception):
    from fastapi.exceptions import RequestValidationError

    if isinstance(exc, RequestValidationError):
        path = request.url.path
        if path == "/register":
            return json_message(
                status.HTTP_400_BAD_REQUEST,
                "Email already in use or invalid data",
            )
        if path == "/login":
            return json_message(
                status.HTTP_401_UNAUTHORIZED,
                "Invalid email or password",
            )
        if path in ("/set_secret", "/get_secret"):
            return json_message(
                status.HTTP_401_UNAUTHORIZED,
                "Invalid authentication token",
            )
    raise exc


def validate_username(username: str) -> bool:
    return USERNAME_PATTERN.fullmatch(username) is not None


def pbkdf2_hash_password(password: str, salt: Optional[bytes] = None) -> str:
    if salt is None:
        salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    salt_b64 = base64.b64encode(salt).decode("ascii")
    derived_b64 = base64.b64encode(derived).decode("ascii")
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt_b64}${derived_b64}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_str, salt_b64, expected_b64 = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_str)
        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected = base64.b64decode(expected_b64.encode("ascii"))
    except Exception:
        return False

    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(derived, expected)


def create_token(user_id: int, email: str, username: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "username": username,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=JWT_EXPIRATION_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, APP_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, APP_SECRET, algorithms=[JWT_ALGORITHM])


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Optional[sqlite3.Row]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        return None

    token = credentials.credentials
    try:
        payload = decode_token(token)
        user_id = int(payload.get("sub", "0"))
        token_username = payload.get("username")
    except (JWTError, ValueError, TypeError):
        return None

    with get_db() as conn:
        user = conn.execute(
            "SELECT id, email, username, password_hash, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()

    if user is None:
        return None

    if not token_username or not hmac.compare_digest(user["username"], token_username):
        return None

    return user


def require_authenticated_user(current_user: Optional[sqlite3.Row]) -> Tuple[bool, Optional[sqlite3.Row]]:
    if current_user is None:
        return False, None
    return True, current_user


@app.post(
    "/register",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Authentication"],
)
def register(request: RegisterRequest, http_request: Request):
    client_key = f"register:{get_client_ip(http_request)}"
    if not auth_rate_limiter.allow(client_key):
        return json_message(
            status.HTTP_400_BAD_REQUEST,
            "Email already in use or invalid data",
        )

    if not validate_username(request.username):
        return json_message(
            status.HTTP_400_BAD_REQUEST,
            "Email already in use or invalid data",
        )

    password_hash = pbkdf2_hash_password(request.password)
    created_at = datetime.now(timezone.utc).isoformat()

    try:
        with get_db() as conn:
            existing_email = conn.execute(
                "SELECT 1 FROM users WHERE email = ?",
                (request.email,),
            ).fetchone()
            if existing_email is not None:
                return json_message(
                    status.HTTP_400_BAD_REQUEST,
                    "Email already in use or invalid data",
                )

            existing_username = conn.execute(
                "SELECT 1 FROM users WHERE username = ?",
                (request.username,),
            ).fetchone()
            if existing_username is not None:
                return json_message(
                    status.HTTP_400_BAD_REQUEST,
                    "Email already in use or invalid data",
                )

            conn.execute(
                """
                INSERT INTO users (email, username, password_hash, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (request.email, request.username, password_hash, created_at),
            )
    except sqlite3.IntegrityError:
        return json_message(
            status.HTTP_400_BAD_REQUEST,
            "Email already in use or invalid data",
        )

    return {"message": "Registration successful"}


@app.post("/login", response_model=LoginResponse, tags=["Authentication"])
def login(request: LoginRequest, http_request: Request):
    client_ip = get_client_ip(http_request)
    rate_key = f"login:{client_ip}:{request.email}"
    if not auth_rate_limiter.allow(rate_key):
        return json_message(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid email or password",
        )

    with get_db() as conn:
        user = conn.execute(
            """
            SELECT id, email, username, password_hash
            FROM users
            WHERE email = ?
            """,
            (request.email,),
        ).fetchone()

    if user is None or not verify_password(request.password, user["password_hash"]):
        return json_message(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid email or password",
        )

    token = create_token(user["id"], user["email"], user["username"])
    return {"token": token, "message": "Login successful"}


@app.post("/set_secret", response_model=MessageResponse)
def set_secret(
    request: SetSecretRequest,
    http_request: Request,
    current_user: Optional[sqlite3.Row] = Depends(get_current_user),
):
    authenticated, user = require_authenticated_user(current_user)
    if not authenticated:
        return json_message(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid authentication token",
        )

    if not validate_username(request.username):
        return json_message(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid authentication token",
        )

    if not hmac.compare_digest(user["username"], request.username):
        return json_message(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid authentication token",
        )

    rate_key = f"set_secret:{get_client_ip(http_request)}:{user['id']}"
    if not secret_write_rate_limiter.allow(rate_key):
        return json_message(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid authentication token",
        )

    updated_at = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM secrets WHERE user_id = ?",
            (user["id"],),
        ).fetchone()

        if existing is None:
            conn.execute(
                """
                INSERT INTO secrets (user_id, secret, updated_at)
                VALUES (?, ?, ?)
                """,
                (user["id"], request.secret, updated_at),
            )
        else:
            conn.execute(
                """
                UPDATE secrets
                SET secret = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (request.secret, updated_at, user["id"]),
            )

    return {"message": "Secret has been set successfully"}


@app.get("/get_secret", response_model=SecretResponse)
def get_secret(
    username: str,
    current_user: Optional[sqlite3.Row] = Depends(get_current_user),
):
    authenticated, user = require_authenticated_user(current_user)
    if not authenticated:
        return json_message(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid authentication token",
        )

    if not validate_username(username):
        return json_message(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid authentication token",
        )

    if not hmac.compare_digest(user["username"], username):
        return json_message(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid authentication token",
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

    if row is None:
        return json_message(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid authentication token",
        )

    return {"secret": row["secret"]}


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)