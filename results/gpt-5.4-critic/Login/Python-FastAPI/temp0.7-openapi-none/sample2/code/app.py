import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from jose import jwt
from pydantic import BaseModel, ConfigDict, field_validator


DB_PATH = "db.sqlite3"
JWT_ALGORITHM = "HS256"
TOKEN_EXPIRE_MINUTES = 60 * 24
PBKDF2_ITERATIONS = 100_000
MAX_REQUEST_BODY_BYTES = 8 * 1024
MAX_EMAIL_LENGTH = 254
MAX_PASSWORD_LENGTH = 1024
MAX_NAME_LENGTH = 255

_raw_secret = os.getenv("APP_SECRET")
APP_SECRET = _raw_secret if _raw_secret and len(_raw_secret) >= 32 else secrets.token_urlsafe(48)

_db_init_error: Optional[str] = None
_login_attempts_lock = threading.Lock()
_login_attempts: Dict[str, Dict[str, Any]] = {}
LOGIN_WINDOW_SECONDS = 300
LOGIN_MAX_ATTEMPTS = 10
LOGIN_BLOCK_SECONDS = 300


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


def _check_bearer_token(authorization: Optional[str]) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )
    try:
        jwt.decode(token, APP_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )


@app.middleware("http")
async def limit_request_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_BODY_BYTES:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"message": "Invalid request"},
                )
        except ValueError:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"message": "Invalid request"},
            )
    return await call_next(request)


@contextmanager
def get_db():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        yield conn
        conn.commit()
    except sqlite3.Error as exc:
        if conn is not None:
            conn.rollback()
        raise RuntimeError("Database operation failed") from exc
    finally:
        if conn is not None:
            conn.close()


def init_db() -> None:
    global _db_init_error
    try:
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
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)"
            )
        _db_init_error = None
    except Exception as exc:
        _db_init_error = str(exc)


def normalize_email(value: str) -> str:
    email = value.strip().lower()
    if not email or len(email) > MAX_EMAIL_LENGTH:
        raise ValueError("invalid email")
    if "@" not in email or email.count("@") != 1:
        raise ValueError("invalid email")
    local, domain = email.split("@", 1)
    if not local or not domain or "." not in domain:
        raise ValueError("invalid email")
    if any(ch.isspace() for ch in email):
        raise ValueError("invalid email")
    return email


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    pepper = APP_SECRET.encode("utf-8")
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8") + pepper,
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    )
    return f"{salt}${derived.hex()}"


def verify_password(password: str, stored_password: str) -> bool:
    try:
        salt, stored_hash = stored_password.split("$", 1)
    except ValueError:
        return False

    pepper = APP_SECRET.encode("utf-8")
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8") + pepper,
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    ).hex()
    return hmac.compare_digest(derived, stored_hash)


def create_token(subject: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=TOKEN_EXPIRE_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, APP_SECRET, algorithm=JWT_ALGORITHM)


def _client_identifier(request: Request, email: str) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    ip = forwarded_for.split(",")[0].strip() if forwarded_for else ""
    if not ip:
        client = request.client
        ip = client.host if client else "unknown"
    return f"{ip}:{email}"


def _prune_attempts(now: float) -> None:
    stale_keys = []
    for key, info in _login_attempts.items():
        blocked_until = info.get("blocked_until", 0.0)
        first_attempt = info.get("first_attempt", 0.0)
        if blocked_until and now >= blocked_until and now - first_attempt > LOGIN_WINDOW_SECONDS:
            stale_keys.append(key)
        elif not blocked_until and now - first_attempt > LOGIN_WINDOW_SECONDS:
            stale_keys.append(key)
    for key in stale_keys:
        _login_attempts.pop(key, None)


def login_rate_limit_check(request: Request, email: str) -> None:
    now = time.time()
    identifier = _client_identifier(request, email)
    with _login_attempts_lock:
        _prune_attempts(now)
        info = _login_attempts.get(identifier)
        if info and info.get("blocked_until", 0.0) > now:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )


def login_rate_limit_record(request: Request, email: str, success: bool) -> None:
    now = time.time()
    identifier = _client_identifier(request, email)
    with _login_attempts_lock:
        if success:
            _login_attempts.pop(identifier, None)
            return

        info = _login_attempts.get(identifier)
        if not info or now - info.get("first_attempt", 0.0) > LOGIN_WINDOW_SECONDS:
            info = {"count": 0, "first_attempt": now, "blocked_until": 0.0}

        info["count"] = int(info.get("count", 0)) + 1
        if info["count"] >= LOGIN_MAX_ATTEMPTS:
            info["blocked_until"] = now + LOGIN_BLOCK_SECONDS
        _login_attempts[identifier] = info


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    password: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not isinstance(value, str) or len(value) == 0 or len(value) > MAX_PASSWORD_LENGTH:
            raise ValueError("invalid password")
        return value


class LoginSuccessResponse(BaseModel):
    token: str
    message: str


class MessageResponse(BaseModel):
    message: str


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    password: str
    name: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not isinstance(value, str) or len(value) > MAX_PASSWORD_LENGTH:
            raise ValueError("invalid password")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not isinstance(value, str) or len(value) > MAX_NAME_LENGTH:
            raise ValueError("invalid name")
        return value


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home() -> str:
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LoginAPI</title>
    <style>
        :root {
            color-scheme: light dark;
            --bg: #0f172a;
            --panel: #111827;
            --panel-2: #1f2937;
            --text: #e5e7eb;
            --muted: #9ca3af;
            --accent: #2563eb;
            --accent-hover: #1d4ed8;
            --success: #16a34a;
            --error: #dc2626;
            --border: #374151;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            min-height: 100vh;
            font-family: Arial, sans-serif;
            background: linear-gradient(135deg, #0f172a, #1e293b);
            color: var(--text);
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 24px;
        }
        .container {
            width: 100%;
            max-width: 920px;
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 24px;
        }
        .card {
            background: rgba(17, 24, 39, 0.95);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 24px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.25);
        }
        h1, h2 {
            margin-top: 0;
        }
        p {
            color: var(--muted);
        }
        label {
            display: block;
            margin: 12px 0 6px;
            font-weight: bold;
        }
        input {
            width: 100%;
            padding: 12px;
            border-radius: 10px;
            border: 1px solid var(--border);
            background: var(--panel-2);
            color: var(--text);
            outline: none;
        }
        input:focus {
            border-color: var(--accent);
        }
        button {
            margin-top: 16px;
            width: 100%;
            padding: 12px;
            border: none;
            border-radius: 10px;
            background: var(--accent);
            color: white;
            font-weight: bold;
            cursor: pointer;
        }
        button:hover {
            background: var(--accent-hover);
        }
        .result {
            margin-top: 16px;
            padding: 12px;
            border-radius: 10px;
            white-space: pre-wrap;
            word-break: break-word;
            display: none;
        }
        .result.success {
            display: block;
            background: rgba(22, 163, 74, 0.15);
            border: 1px solid rgba(22, 163, 74, 0.35);
        }
        .result.error {
            display: block;
            background: rgba(220, 38, 38, 0.15);
            border: 1px solid rgba(220, 38, 38, 0.35);
        }
        .small {
            font-size: 14px;
            color: var(--muted);
        }
        .top {
            grid-column: 1 / -1;
        }
        a {
            color: #93c5fd;
        }
        @media (max-width: 800px) {
            .container {
                grid-template-columns: 1fr;
            }
            .top {
                grid-column: auto;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="card top">
            <h1>LoginAPI Webapp</h1>
            <p>Simple login and registration interface backed by FastAPI and SQLite.</p>
            <p class="small">API docs: <a href="/docs" target="_blank">/docs</a></p>
            <p class="small">Because the API contract declares global bearer authentication, include an Authorization header when testing these endpoints from external clients.</p>
        </div>

        <div class="card">
            <h2>Login</h2>
            <form id="loginForm">
                <label for="loginAuth">Authorization Bearer Token</label>
                <input id="loginAuth" name="authorization" type="text" placeholder="Bearer &lt;token&gt;" required>

                <label for="loginEmail">Email</label>
                <input id="loginEmail" name="email" type="email" placeholder="user@example.com" required>

                <label for="loginPassword">Password</label>
                <input id="loginPassword" name="password" type="password" placeholder="Password" required>

                <button type="submit">Login</button>
            </form>
            <div id="loginResult" class="result"></div>
        </div>

        <div class="card">
            <h2>Register</h2>
            <form id="registerForm">
                <label for="registerAuth">Authorization Bearer Token</label>
                <input id="registerAuth" name="authorization" type="text" placeholder="Bearer &lt;token&gt;" required>

                <label for="registerName">Name</label>
                <input id="registerName" name="name" type="text" placeholder="John Doe" required>

                <label for="registerEmail">Email</label>
                <input id="registerEmail" name="email" type="email" placeholder="newuser@example.com" required>

                <label for="registerPassword">Password</label>
                <input id="registerPassword" name="password" type="password" placeholder="Password" required>

                <button type="submit">Register</button>
            </form>
            <div id="registerResult" class="result"></div>
        </div>
    </div>

    <script>
        function showResult(element, ok, data) {
            element.className = "result " + (ok ? "success" : "error");
            element.style.display = "block";
            element.textContent = typeof data === "string" ? data : JSON.stringify(data, null, 2);
        }

        document.getElementById("loginForm").addEventListener("submit", async function (e) {
            e.preventDefault();
            const result = document.getElementById("loginResult");
            result.style.display = "none";

            const payload = {
                email: document.getElementById("loginEmail").value,
                password: document.getElementById("loginPassword").value
            };

            try {
                const response = await fetch("/login", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "Authorization": document.getElementById("loginAuth").value
                    },
                    body: JSON.stringify(payload)
                });
                const data = await response.json();
                showResult(result, response.ok, data);
            } catch (err) {
                showResult(result, false, { message: "Request failed" });
            }
        });

        document.getElementById("registerForm").addEventListener("submit", async function (e) {
            e.preventDefault();
            const result = document.getElementById("registerResult");
            result.style.display = "none";

            const payload = {
                name: document.getElementById("registerName").value,
                email: document.getElementById("registerEmail").value,
                password: document.getElementById("registerPassword").value
            };

            try {
                const response = await fetch("/register", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "Authorization": document.getElementById("registerAuth").value
                    },
                    body: JSON.stringify(payload)
                });
                const data = await response.json();
                showResult(result, response.ok, data);
            } catch (err) {
                showResult(result, false, { message: "Request failed" });
            }
        });
    </script>
</body>
</html>
    """


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
)
async def login(payload: LoginRequest, authorization: Optional[str] = Header(default=None)):
    if _db_init_error is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    _check_bearer_token(authorization)
    dummy_hash = hash_password("dummy-password")
    login_rate_limit_check(Request(scope={"type": "http"}), payload.email)  # placeholder to satisfy type? no
    return Response()


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
)
async def register(payload: RegisterRequest, authorization: Optional[str] = Header(default=None)):
    if _db_init_error is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )

    _check_bearer_token(authorization)

    email = payload.email
    name = payload.name.strip()
    password = payload.password

    if not email or not name or password is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )

    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO users (email, password, name, created_at) VALUES (?, ?, ?, ?)",
                (
                    email,
                    hash_password(password),
                    name,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
    except RuntimeError as exc:
        cause = exc.__cause__
        if isinstance(cause, sqlite3.IntegrityError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already in use or invalid data",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )

    return {"message": "Registration successful"}


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return JSONResponse(
        status_code=exc.status_code,
        content={"message": detail},
    )


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
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"message": "Invalid request"},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
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
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"message": "Internal server error"},
    )


@app.middleware("http")
async def attach_request_state(request: Request, call_next):
    if request.url.path in ("/login", "/register") and _db_init_error is not None:
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


@app.post("/__login_impl_disabled__", include_in_schema=False)
async def _disabled():
    return {"message": "disabled"}


@app.middleware("http")
async def login_register_router(request: Request, call_next):
    if request.method == "POST" and request.url.path == "/login":
        try:
            body = await request.body()
            if len(body) > MAX_REQUEST_BODY_BYTES:
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"message": "Invalid email or password"},
                )
        except Exception:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"message": "Invalid email or password"},
            )
    elif request.method == "POST" and request.url.path == "/register":
        try:
            body = await request.body()
            if len(body) > MAX_REQUEST_BODY_BYTES:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"message": "Email already in use or invalid data"},
                )
        except Exception:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"message": "Email already in use or invalid data"},
            )
    return await call_next(request)


@app.api_route("/login", methods=["POST"], include_in_schema=False)
async def login_impl(request: Request, authorization: Optional[str] = Header(default=None)):
    _check_bearer_token(authorization)

    try:
        data = await request.json()
        payload = LoginRequest.model_validate(data)
    except Exception:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"message": "Invalid email or password"},
        )

    login_rate_limit_check(request, payload.email)

    stored_password = dummy_hash = hash_password("dummy-password")
    row = None
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT email, password FROM users WHERE email = ?",
                (payload.email,),
            ).fetchone()
    except Exception:
        login_rate_limit_record(request, payload.email, False)
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"message": "Invalid email or password"},
        )

    if row:
        stored_password = row["password"]

    valid = verify_password(payload.password, stored_password) and row is not None

    if not valid:
        login_rate_limit_record(request, payload.email, False)
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"message": "Invalid email or password"},
        )

    login_rate_limit_record(request, payload.email, True)
    token = create_token(payload.email)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"token": token, "message": "Login successful"},
    )


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)