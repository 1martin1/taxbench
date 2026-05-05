import json
import os
import sqlite3
import secrets
import hashlib
import hmac
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from jose import jwt
from pydantic import BaseModel, Field, field_validator
import uvicorn


DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24

PBKDF2_ITERATIONS = 100000
MAX_REQUEST_BODY_BYTES = 16 * 1024
MAX_EMAIL_LENGTH = 254
MAX_PASSWORD_LENGTH = 256
MAX_NAME_LENGTH = 200

RATE_LIMIT_WINDOW_SECONDS = 60
LOGIN_RATE_LIMIT_MAX = 10
REGISTER_RATE_LIMIT_MAX = 5

_rate_limit_lock = threading.Lock()
_rate_limit_store: Dict[Tuple[str, str], list[float]] = {}


app = FastAPI(
    title="LoginAPI",
    description="A simple webapp API for login and registration functionality.",
    version="1.0.11",
)


class MessageResponse(BaseModel):
    message: str


class LoginResponse(BaseModel):
    token: str
    message: str


class LoginRequest(BaseModel):
    email: str = Field(..., examples=["user@example.com"], max_length=MAX_EMAIL_LENGTH)
    password: str = Field(..., examples=["mySecretPassword"], min_length=1, max_length=MAX_PASSWORD_LENGTH)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = normalize_email(value)
        if not is_valid_email(normalized):
            raise ValueError("invalid email")
        return normalized


class RegisterRequest(BaseModel):
    email: str = Field(..., examples=["newuser@example.com"], max_length=MAX_EMAIL_LENGTH)
    password: str = Field(..., examples=["newUserPassword"], min_length=1, max_length=MAX_PASSWORD_LENGTH)
    name: str = Field(..., examples=["John Doe"], min_length=1, max_length=MAX_NAME_LENGTH)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = normalize_email(value)
        if not is_valid_email(normalized):
            raise ValueError("invalid email")
        return normalized

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("invalid name")
        return stripped


def normalize_email(email: str) -> str:
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    if not email or len(email) > MAX_EMAIL_LENGTH:
        return False
    if "@" not in email or email.count("@") != 1:
        return False
    local, domain = email.split("@", 1)
    if not local or not domain:
        return False
    if len(local) > 64 or len(domain) > 253:
        return False
    if domain.startswith(".") or domain.endswith(".") or ".." in domain:
        return False
    if "." not in domain:
        return False
    allowed_local = set("abcdefghijklmnopqrstuvwxyz0123456789.!#$%&'*+/=?^_`{|}~-")
    allowed_domain = set("abcdefghijklmnopqrstuvwxyz0123456789.-")
    local_lower = local.lower()
    domain_lower = domain.lower()
    if any(ch not in allowed_local for ch in local_lower):
        return False
    if any(ch not in allowed_domain for ch in domain_lower):
        return False
    for label in domain_lower.split("."):
        if not label or label.startswith("-") or label.endswith("-"):
            return False
    return True


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_db_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    )
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, expected_hash = stored_hash.split("$", 1)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    ).hex()
    return hmac.compare_digest(digest, expected_hash)


def create_token(user_id: int, email: str) -> str:
    if not APP_SECRET:
        raise RuntimeError("APP_SECRET environment variable must be set")
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=JWT_EXPIRE_HOURS)).timestamp()),
    }
    return jwt.encode(payload, APP_SECRET, algorithm=JWT_ALGORITHM)


def get_user_by_email(email: str) -> Optional[sqlite3.Row]:
    conn = get_db_connection()
    try:
        cur = conn.execute(
            "SELECT id, email, name, password_hash, created_at FROM users WHERE email = ?",
            (normalize_email(email),),
        )
        return cur.fetchone()
    finally:
        conn.close()


def create_user(email: str, password: str, name: str) -> bool:
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO users (email, name, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (
                normalize_email(email),
                name.strip(),
                hash_password(password),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def client_ip(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def is_rate_limited(scope: str, key: str, limit: int) -> bool:
    now = time.time()
    bucket_key = (scope, key)
    with _rate_limit_lock:
        timestamps = _rate_limit_store.get(bucket_key, [])
        timestamps = [ts for ts in timestamps if now - ts < RATE_LIMIT_WINDOW_SECONDS]
        if len(timestamps) >= limit:
            _rate_limit_store[bucket_key] = timestamps
            return True
        timestamps.append(now)
        _rate_limit_store[bucket_key] = timestamps
        if len(_rate_limit_store) > 10000:
            stale_keys = [
                k for k, v in _rate_limit_store.items()
                if not v or now - v[-1] >= RATE_LIMIT_WINDOW_SECONDS
            ]
            for stale_key in stale_keys[:1000]:
                _rate_limit_store.pop(stale_key, None)
        return False


async def parse_json_body(request: Request) -> Any:
    body = await request.body()
    if len(body) > MAX_REQUEST_BODY_BYTES:
        raise ValueError("body too large")
    try:
        return json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("invalid json")


@app.middleware("http")
async def body_size_limit_middleware(request: Request, call_next):
    if request.url.path in {"/login", "/register"}:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_REQUEST_BODY_BYTES:
                    if request.url.path == "/register":
                        return JSONResponse(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            content={"message": "Email already in use or invalid data"},
                        )
                    return JSONResponse(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        content={"message": "Invalid email or password"},
                    )
            except ValueError:
                if request.url.path == "/register":
                    return JSONResponse(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        content={"message": "Email already in use or invalid data"},
                    )
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"message": "Invalid email or password"},
                )
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
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
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors()},
    )


@app.on_event("startup")
def startup_event() -> None:
    if not APP_SECRET:
        raise RuntimeError("APP_SECRET environment variable must be set")
    init_db()


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home() -> str:
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
            color: #222;
        }
        .container {
            max-width: 420px;
            margin: 40px auto;
            background: #fff;
            border-radius: 12px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.08);
            padding: 24px;
        }
        h1, h2 {
            margin-top: 0;
            text-align: center;
        }
        .tabs {
            display: flex;
            margin-bottom: 20px;
            border-bottom: 1px solid #ddd;
        }
        .tab {
            flex: 1;
            padding: 12px;
            text-align: center;
            cursor: pointer;
            background: #f8f8f8;
            border: none;
            font-size: 16px;
        }
        .tab.active {
            background: #fff;
            border-bottom: 2px solid #2563eb;
            font-weight: bold;
        }
        .panel {
            display: none;
        }
        .panel.active {
            display: block;
        }
        label {
            display: block;
            margin: 12px 0 6px;
            font-weight: 600;
        }
        input {
            width: 100%;
            box-sizing: border-box;
            padding: 10px;
            border: 1px solid #cfd8e3;
            border-radius: 8px;
            font-size: 14px;
        }
        button.submit {
            width: 100%;
            margin-top: 16px;
            padding: 12px;
            background: #2563eb;
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 15px;
            cursor: pointer;
        }
        button.submit:hover {
            background: #1d4ed8;
        }
        .result {
            margin-top: 16px;
            padding: 12px;
            border-radius: 8px;
            white-space: pre-wrap;
            word-break: break-word;
            font-size: 14px;
        }
        .success {
            background: #ecfdf5;
            color: #065f46;
            border: 1px solid #a7f3d0;
        }
        .error {
            background: #fef2f2;
            color: #991b1b;
            border: 1px solid #fecaca;
        }
        .small {
            text-align: center;
            margin-top: 16px;
            color: #666;
            font-size: 13px;
        }
        a {
            color: #2563eb;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>LoginAPI</h1>
        <div class="tabs">
            <button class="tab active" onclick="showTab('login')">Login</button>
            <button class="tab" onclick="showTab('register')">Register</button>
        </div>

        <div id="login-panel" class="panel active">
            <h2>Sign In</h2>
            <form id="login-form">
                <label for="login-email">Email</label>
                <input id="login-email" type="email" required placeholder="user@example.com" maxlength="254" />

                <label for="login-password">Password</label>
                <input id="login-password" type="password" required placeholder="Enter password" maxlength="256" />

                <button class="submit" type="submit">Login</button>
            </form>
            <div id="login-result"></div>
        </div>

        <div id="register-panel" class="panel">
            <h2>Create Account</h2>
            <form id="register-form">
                <label for="register-name">Name</label>
                <input id="register-name" type="text" required placeholder="John Doe" maxlength="200" />

                <label for="register-email">Email</label>
                <input id="register-email" type="email" required placeholder="newuser@example.com" maxlength="254" />

                <label for="register-password">Password</label>
                <input id="register-password" type="password" required placeholder="Enter password" maxlength="256" />

                <button class="submit" type="submit">Register</button>
            </form>
            <div id="register-result"></div>
        </div>

        <div class="small">
            API docs available at <a href="/docs">/docs</a>
        </div>
    </div>

    <script>
        function showTab(tabName) {
            const loginTab = document.querySelectorAll('.tab')[0];
            const registerTab = document.querySelectorAll('.tab')[1];
            const loginPanel = document.getElementById('login-panel');
            const registerPanel = document.getElementById('register-panel');

            if (tabName === 'login') {
                loginTab.classList.add('active');
                registerTab.classList.remove('active');
                loginPanel.classList.add('active');
                registerPanel.classList.remove('active');
            } else {
                registerTab.classList.add('active');
                loginTab.classList.remove('active');
                registerPanel.classList.add('active');
                loginPanel.classList.remove('active');
            }
        }

        function setResult(elementId, ok, data) {
            const el = document.getElementById(elementId);
            el.className = 'result ' + (ok ? 'success' : 'error');
            el.textContent = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
        }

        document.getElementById('login-form').addEventListener('submit', async function(e) {
            e.preventDefault();
            const payload = {
                email: document.getElementById('login-email').value,
                password: document.getElementById('login-password').value
            };

            try {
                const response = await fetch('/login', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const data = await response.json();
                setResult('login-result', response.ok, data);
            } catch (err) {
                setResult('login-result', false, 'Request failed');
            }
        });

        document.getElementById('register-form').addEventListener('submit', async function(e) {
            e.preventDefault();
            const payload = {
                name: document.getElementById('register-name').value,
                email: document.getElementById('register-email').value,
                password: document.getElementById('register-password').value
            };

            try {
                const response = await fetch('/register', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const data = await response.json();
                setResult('register-result', response.ok, data);
            } catch (err) {
                setResult('register-result', false, 'Request failed');
            }
        });
    </script>
</body>
</html>
    """


@app.post(
    "/login",
    tags=["Authentication"],
    response_model=LoginResponse,
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
async def login(request: Request) -> JSONResponse:
    ip = client_ip(request)
    if is_rate_limited("login", ip, LOGIN_RATE_LIMIT_MAX):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"message": "Invalid email or password"},
        )

    try:
        data = await parse_json_body(request)
        payload = LoginRequest.model_validate(data)
    except Exception:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"message": "Invalid email or password"},
        )

    user = get_user_by_email(payload.email)
    if not user or not verify_password(payload.password, user["password_hash"]):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"message": "Invalid email or password"},
        )

    token = create_token(user["id"], user["email"])
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"token": token, "message": "Login successful"},
    )


@app.post(
    "/register",
    tags=["Authentication"],
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
async def register(request: Request) -> JSONResponse:
    ip = client_ip(request)
    if is_rate_limited("register", ip, REGISTER_RATE_LIMIT_MAX):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Email already in use or invalid data"},
        )

    try:
        data = await parse_json_body(request)
        payload = RegisterRequest.model_validate(data)
    except Exception:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Email already in use or invalid data"},
        )

    created = create_user(payload.email, payload.password, payload.name)
    if not created:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Email already in use or invalid data"},
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={"message": "Registration successful"},
    )


if __name__ == "__main__":
    if not APP_SECRET:
        raise RuntimeError("APP_SECRET environment variable must be set")
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)