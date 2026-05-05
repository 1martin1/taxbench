import os
import re
import sqlite3
import secrets
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from jose import jwt
from pydantic import BaseModel
import uvicorn


DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET")
JWT_ALGORITHM = "HS256"
TOKEN_EXPIRE_MINUTES = 60 * 24

PBKDF2_ITERATIONS = 100_000
DB_TIMEOUT_SECONDS = 5.0

EMAIL_MAX_LENGTH = 254
PASSWORD_MAX_LENGTH = 256
NAME_MAX_LENGTH = 200

RATE_LIMIT_WINDOW_SECONDS = 60
LOGIN_RATE_LIMIT_MAX_REQUESTS = 10
REGISTER_RATE_LIMIT_MAX_REQUESTS = 5

_email_regex = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SimpleRateLimiter:
    def __init__(self) -> None:
        self._storage: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        cutoff = now - window_seconds
        with self._lock:
            bucket = self._storage.get(key)
            if bucket is None:
                bucket = deque()
                self._storage[key] = bucket

            while bucket and bucket[0] < cutoff:
                bucket.popleft()

            if len(bucket) >= limit:
                return False

            bucket.append(now)
            return True


rate_limiter = SimpleRateLimiter()


app = FastAPI(
    title="LoginAPI",
    description="A simple webapp API for login and registration functionality.",
    version="1.0.11",
)


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
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
                password TEXT NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )


def hash_password(password: str) -> str:
    import hashlib

    salt_bytes = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt_bytes,
        PBKDF2_ITERATIONS,
    )
    return f"{salt_bytes.hex()}${digest.hex()}"


def verify_password(password: str, stored_value: str) -> bool:
    import hashlib
    import hmac

    try:
        salt_hex, stored_hash = stored_value.split("$", 1)
        salt_bytes = bytes.fromhex(salt_hex)
    except (ValueError, TypeError):
        return False

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt_bytes,
        PBKDF2_ITERATIONS,
    )
    return hmac.compare_digest(digest.hex(), stored_hash)


def create_token(user_id: int, email: str) -> str:
    if not APP_SECRET:
        raise RuntimeError("APP_SECRET environment variable must be set")

    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=TOKEN_EXPIRE_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, APP_SECRET, algorithm=JWT_ALGORITHM)


def client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def enforce_rate_limit(request: Request, action: str, limit: int) -> None:
    key = f"{action}:{client_ip(request)}"
    if not rate_limiter.allow(key, limit, RATE_LIMIT_WINDOW_SECONDS):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED if action == "login" else status.HTTP_400_BAD_REQUEST,
            detail="Invalid email or password" if action == "login" else "Email already in use or invalid data",
        )


def is_valid_email(value: str) -> bool:
    if not isinstance(value, str):
        return False
    if len(value) == 0 or len(value) > EMAIL_MAX_LENGTH:
        return False
    if not _email_regex.match(value):
        return False
    return True


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str


class LoginSuccessResponse(BaseModel):
    token: str
    message: str


class MessageResponse(BaseModel):
    message: str


def parse_json_body(body: bytes) -> dict[str, Any]:
    import json

    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )

    if not isinstance(data, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )
    return data


def validate_login_payload(data: dict[str, Any]) -> LoginRequest:
    email = data.get("email")
    password = data.get("password")

    if not isinstance(email, str) or not isinstance(password, str):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    email = email.strip().lower()

    if not is_valid_email(email):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if len(password) < 1 or len(password) > PASSWORD_MAX_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    return LoginRequest(email=email, password=password)


def validate_register_payload(data: dict[str, Any]) -> RegisterRequest:
    email = data.get("email")
    password = data.get("password")
    name = data.get("name")

    if not isinstance(email, str) or not isinstance(password, str) or not isinstance(name, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )

    email = email.strip().lower()
    name = name.strip()

    if not is_valid_email(email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )

    if len(password) < 1 or len(password) > PASSWORD_MAX_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )

    if len(name) < 1 or len(name) > NAME_MAX_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )

    return RegisterRequest(email=email, password=password, name=name)


@app.on_event("startup")
def startup_event() -> None:
    if not APP_SECRET:
        raise RuntimeError("APP_SECRET environment variable must be set")
    init_db()


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> str:
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LoginAPI</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            background: #f4f7fb;
            margin: 0;
            padding: 0;
            color: #1f2937;
        }
        .container {
            max-width: 420px;
            margin: 40px auto;
            background: white;
            padding: 24px;
            border-radius: 12px;
            box-shadow: 0 10px 25px rgba(0,0,0,0.08);
        }
        h1 {
            margin-top: 0;
            font-size: 28px;
            text-align: center;
        }
        h2 {
            margin-bottom: 12px;
            font-size: 20px;
        }
        form {
            margin-bottom: 28px;
        }
        label {
            display: block;
            margin: 10px 0 6px;
            font-weight: 600;
        }
        input {
            width: 100%;
            padding: 10px 12px;
            border: 1px solid #d1d5db;
            border-radius: 8px;
            box-sizing: border-box;
        }
        button {
            margin-top: 14px;
            width: 100%;
            padding: 11px 14px;
            border: none;
            background: #2563eb;
            color: white;
            border-radius: 8px;
            font-weight: 700;
            cursor: pointer;
        }
        button:hover {
            background: #1d4ed8;
        }
        pre {
            background: #111827;
            color: #f9fafb;
            padding: 12px;
            border-radius: 8px;
            overflow-x: auto;
            white-space: pre-wrap;
            word-break: break-word;
        }
        .small {
            color: #6b7280;
            font-size: 14px;
            text-align: center;
        }
        .top-links {
            text-align: center;
            margin-bottom: 18px;
        }
        .top-links a {
            color: #2563eb;
            text-decoration: none;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>LoginAPI</h1>
        <div class="top-links">
            <a href="/docs" target="_blank">OpenAPI Docs</a>
        </div>

        <h2>Register</h2>
        <form id="registerForm">
            <label for="register_name">Name</label>
            <input id="register_name" name="name" type="text" required maxlength="200" />

            <label for="register_email">Email</label>
            <input id="register_email" name="email" type="email" required maxlength="254" />

            <label for="register_password">Password</label>
            <input id="register_password" name="password" type="password" required maxlength="256" />

            <button type="submit">Register</button>
        </form>

        <h2>Login</h2>
        <form id="loginForm">
            <label for="login_email">Email</label>
            <input id="login_email" name="email" type="email" required maxlength="254" />

            <label for="login_password">Password</label>
            <input id="login_password" name="password" type="password" required maxlength="256" />

            <button type="submit">Login</button>
        </form>

        <h2>Response</h2>
        <pre id="output">Ready.</pre>
        <p class="small">Successful login returns a bearer token.</p>
    </div>

    <script>
        const output = document.getElementById("output");

        async function sendJson(url, payload) {
            const response = await fetch(url, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(payload)
            });
            const data = await response.json().catch(() => ({message: "Invalid server response"}));
            output.textContent = JSON.stringify({status: response.status, data}, null, 2);
        }

        document.getElementById("registerForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            await sendJson("/register", {
                name: document.getElementById("register_name").value,
                email: document.getElementById("register_email").value,
                password: document.getElementById("register_password").value
            });
        });

        document.getElementById("loginForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            await sendJson("/login", {
                email: document.getElementById("login_email").value,
                password: document.getElementById("login_password").value
            });
        });
    </script>
</body>
</html>
    """


@app.post(
    "/login",
    tags=["Authentication"],
    summary="User login",
    description="Authenticate a user with email and password.",
    response_model=LoginSuccessResponse,
    openapi_extra={"security": []},
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
            "model": MessageResponse,
            "content": {
                "application/json": {
                    "example": {"message": "Invalid email or password"}
                }
            },
        },
    },
)
async def login(request: Request):
    enforce_rate_limit(request, "login", LOGIN_RATE_LIMIT_MAX_REQUESTS)
    payload = validate_login_payload(parse_json_body(await request.body()))

    with get_db() as conn:
        user = conn.execute(
            "SELECT id, email, password FROM users WHERE email = ?",
            (payload.email,),
        ).fetchone()

    if not user or not verify_password(payload.password, user["password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = create_token(user["id"], user["email"])
    return {"token": token, "message": "Login successful"}


@app.post(
    "/register",
    tags=["Authentication"],
    summary="User registration",
    description="Register a new user with email, password, and name.",
    status_code=201,
    response_model=MessageResponse,
    openapi_extra={"security": []},
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
            "model": MessageResponse,
            "content": {
                "application/json": {
                    "example": {"message": "Email already in use or invalid data"}
                }
            },
        },
    },
)
async def register(request: Request):
    enforce_rate_limit(request, "register", REGISTER_RATE_LIMIT_MAX_REQUESTS)
    payload = validate_register_payload(parse_json_body(await request.body()))

    password_hash = hash_password(payload.password)

    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO users (email, password, name, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    payload.email,
                    password_hash,
                    payload.name,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )
    except sqlite3.OperationalError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )

    return {"message": "Registration successful"}


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, str):
        return JSONResponse(status_code=exc.status_code, content={"message": exc.detail})
    return JSONResponse(status_code=exc.status_code, content={"message": "Request failed"})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    path = request.url.path
    if path == "/login":
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"message": "Invalid email or password"},
        )
    if path == "/register":
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Email already in use or invalid data"},
        )
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"message": "Request failed"},
    )


if __name__ == "__main__":
    if not APP_SECRET:
        raise RuntimeError("APP_SECRET environment variable must be set")
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)