import os
import re
import time
import sqlite3
import secrets
import hashlib
import hmac
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from jose import jwt
from pydantic import BaseModel, ConfigDict, Field, field_validator
import uvicorn


DB_PATH = "db.sqlite3"
JWT_ALGORITHM = "HS256"
JWT_EXP_MINUTES = 60 * 24
PBKDF2_ITERATIONS = 100_000

EMAIL_MAX_LENGTH = 254
PASSWORD_MAX_LENGTH = 1024
NAME_MAX_LENGTH = 255

RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_ATTEMPTS = 30
RATE_LIMIT_MAX_DISTINCT_KEYS = 10000

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError("APP_SECRET environment variable must be set")


class SimpleRateLimiter:
    def __init__(self, window_seconds: int, max_attempts: int, max_keys: int) -> None:
        self.window_seconds = window_seconds
        self.max_attempts = max_attempts
        self.max_keys = max_keys
        self._storage: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.time()
        cutoff = now - self.window_seconds

        with self._lock:
            timestamps = self._storage.get(key, [])
            timestamps = [ts for ts in timestamps if ts >= cutoff]

            if len(timestamps) >= self.max_attempts:
                self._storage[key] = timestamps
                return False

            timestamps.append(now)
            self._storage[key] = timestamps

            if len(self._storage) > self.max_keys:
                stale_keys = [k for k, values in self._storage.items() if not values or values[-1] < cutoff]
                for stale_key in stale_keys:
                    self._storage.pop(stale_key, None)

                if len(self._storage) > self.max_keys:
                    oldest_items = sorted(
                        self._storage.items(),
                        key=lambda item: item[1][-1] if item[1] else 0.0,
                    )
                    excess = len(self._storage) - self.max_keys
                    for stale_key, _ in oldest_items[:excess]:
                        self._storage.pop(stale_key, None)

            return True


rate_limiter = SimpleRateLimiter(
    window_seconds=RATE_LIMIT_WINDOW_SECONDS,
    max_attempts=RATE_LIMIT_MAX_ATTEMPTS,
    max_keys=RATE_LIMIT_MAX_DISTINCT_KEYS,
)

app = FastAPI(
    title="LoginAPI",
    description="A simple webapp API for login and registration functionality.",
    version="1.0.11",
    openapi_tags=[
        {
            "name": "Authentication",
            "description": "Operations related to user authentication",
        }
    ],
)


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(get_db_connection()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    ).hex()


def verify_password(password: str, salt: str, password_hash: str) -> bool:
    computed = hash_password(password, salt)
    return hmac.compare_digest(computed, password_hash)


def create_token(user_id: int, email: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=JWT_EXP_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, APP_SECRET, algorithm=JWT_ALGORITHM)


def normalize_email(value: str) -> str:
    email = value.strip().lower()
    if not email or len(email) > EMAIL_MAX_LENGTH or not EMAIL_REGEX.fullmatch(email):
        raise ValueError("invalid email")
    return email


def get_client_identifier(request: Request, email: str | None = None) -> str:
    client_host = request.client.host if request.client else "unknown"
    if email:
        return f"{client_host}:{email}"
    return client_host


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., examples=["user@example.com"], min_length=3, max_length=EMAIL_MAX_LENGTH)
    password: str = Field(..., examples=["mySecretPassword"], min_length=1, max_length=PASSWORD_MAX_LENGTH)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class LoginSuccessResponse(BaseModel):
    token: str = Field(..., examples=["jwt-token-abc123"])
    message: str = Field(..., examples=["Login successful"])


class MessageResponse(BaseModel):
    message: str


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., examples=["newuser@example.com"], min_length=3, max_length=EMAIL_MAX_LENGTH)
    password: str = Field(..., examples=["newUserPassword"], min_length=1, max_length=PASSWORD_MAX_LENGTH)
    name: str = Field(..., examples=["John Doe"], min_length=1, max_length=NAME_MAX_LENGTH)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("invalid name")
        return cleaned


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.post(
    "/login",
    tags=["Authentication"],
    response_model=LoginSuccessResponse,
    responses={
        200: {
            "description": "Successful login",
            "content": {
                "application/json": {
                    "example": {
                        "token": "jwt-token-abc123",
                        "message": "Login successful",
                    }
                }
            },
        },
        401: {
            "description": "Unauthorized",
            "content": {
                "application/json": {
                    "example": {"message": "Invalid email or password"}
                }
            },
        },
    },
    summary="User login",
    description="Authenticate a user with email and password.",
    openapi_extra={"security": []},
)
def login(payload: LoginRequest, request: Request) -> Any:
    limiter_key = get_client_identifier(request, payload.email)
    if not rate_limiter.allow(limiter_key):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"message": "Invalid email or password"},
        )

    try:
        with closing(get_db_connection()) as conn:
            row = conn.execute(
                "SELECT id, email, password_hash, salt FROM users WHERE email = ?",
                (payload.email,),
            ).fetchone()
    except sqlite3.Error:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"message": "Invalid email or password"},
        )

    if row is None or not verify_password(payload.password, row["salt"], row["password_hash"]):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"message": "Invalid email or password"},
        )

    token = create_token(row["id"], row["email"])
    return {"token": token, "message": "Login successful"}


@app.post(
    "/register",
    tags=["Authentication"],
    status_code=status.HTTP_201_CREATED,
    response_model=MessageResponse,
    responses={
        201: {
            "description": "Successful registration",
            "content": {
                "application/json": {
                    "example": {"message": "Registration successful"}
                }
            },
        },
        400: {
            "description": "Bad Request",
            "content": {
                "application/json": {
                    "example": {"message": "Email already in use or invalid data"}
                }
            },
        },
    },
    summary="User registration",
    description="Register a new user with email, password, and name.",
    openapi_extra={"security": []},
)
def register(payload: RegisterRequest, request: Request) -> JSONResponse:
    limiter_key = get_client_identifier(request, payload.email)
    if not rate_limiter.allow(limiter_key):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Email already in use or invalid data"},
        )

    salt = secrets.token_hex(16)
    password_hash = hash_password(payload.password, salt)

    try:
        with closing(get_db_connection()) as conn:
            conn.execute(
                """
                INSERT INTO users (email, name, password_hash, salt, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    payload.email,
                    payload.name,
                    password_hash,
                    salt,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Email already in use or invalid data"},
        )
    except sqlite3.Error:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Email already in use or invalid data"},
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={"message": "Registration successful"},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return JSONResponse(status_code=exc.status_code, content={"message": detail})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    if request.url.path == "/register":
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Email already in use or invalid data"},
        )
    if request.url.path == "/login":
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"message": "Invalid email or password"},
        )
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"message": "Request failed"},
    )


def custom_openapi() -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema

    original_openapi_schema = original_openapi()
    original_openapi_schema["openapi"] = "3.0.3"
    original_openapi_schema["components"] = original_openapi_schema.get("components", {})
    original_openapi_schema["components"]["securitySchemes"] = {
        "bearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
    }
    original_openapi_schema["security"] = [{"bearerAuth": []}]

    paths = original_openapi_schema.get("paths", {})
    if "/login" in paths and "post" in paths["/login"]:
        paths["/login"]["post"]["security"] = []
    if "/register" in paths and "post" in paths["/register"]:
        paths["/register"]["post"]["security"] = []

    app.openapi_schema = original_openapi_schema
    return app.openapi_schema


original_openapi = app.openapi
app.openapi = custom_openapi


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)