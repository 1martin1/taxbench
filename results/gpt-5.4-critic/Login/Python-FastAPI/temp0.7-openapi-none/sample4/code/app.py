import os
import sqlite3
import secrets
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Dict, List, Optional, Tuple

from fastapi import Body, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse
from jose import jwt
from passlib.context import CryptContext
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
import uvicorn


DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24

MAX_EMAIL_LENGTH = 254
MAX_PASSWORD_LENGTH = 128
MAX_NAME_LENGTH = 100
MAX_REQUEST_BODY_BYTES = 4096

RATE_LIMIT_WINDOW_SECONDS = 60
LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 5
REGISTER_RATE_LIMIT_MAX_ATTEMPTS = 3

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_REQUEST_BODY_BYTES:
                    return JSONResponse(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        content={"message": "Invalid request body"},
                    )
            except ValueError:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"message": "Invalid request body"},
                )

        if request.method in {"POST", "PUT", "PATCH"}:
            body = await request.body()
            if len(body) > MAX_REQUEST_BODY_BYTES:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"message": "Invalid request body"},
                )

        return await call_next(request)


class RateLimiter:
    def __init__(self) -> None:
        self._events: Dict[Tuple[str, str], List[float]] = {}
        self._lock = Lock()

    def allow(self, key: Tuple[str, str], limit: int, window_seconds: int) -> bool:
        now = time.time()
        cutoff = now - window_seconds
        with self._lock:
            timestamps = self._events.get(key, [])
            timestamps = [ts for ts in timestamps if ts >= cutoff]
            if len(timestamps) >= limit:
                self._events[key] = timestamps
                return False
            timestamps.append(now)
            self._events[key] = timestamps
            return True


rate_limiter = RateLimiter()


def require_app_secret() -> str:
    if not APP_SECRET or len(APP_SECRET) < 32:
        raise RuntimeError(
            "APP_SECRET environment variable must be set to a strong secret of at least 32 characters."
        )
    return APP_SECRET


app = FastAPI(
    title="LoginAPI",
    description="A simple webapp API for login and registration functionality.",
    version="1.0.11",
)

app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=require_app_secret(),
    https_only=True,
    same_site="lax",
    session_cookie="session",
    max_age=JWT_EXPIRATION_HOURS * 3600,
)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(..., max_length=MAX_EMAIL_LENGTH)
    password: str = Field(..., min_length=1, max_length=MAX_PASSWORD_LENGTH)


class LoginSuccessResponse(BaseModel):
    token: str
    message: str


class ErrorResponse(BaseModel):
    message: str


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(..., max_length=MAX_EMAIL_LENGTH)
    password: str = Field(..., min_length=1, max_length=MAX_PASSWORD_LENGTH)
    name: str = Field(..., min_length=1, max_length=MAX_NAME_LENGTH)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Invalid name")
        return stripped


class RegisterSuccessResponse(BaseModel):
    message: str


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(get_db_connection()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_token(user_id: int, email: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=JWT_EXPIRATION_HOURS)).timestamp()),
        "jti": secrets.token_hex(16),
    }
    return jwt.encode(payload, require_app_secret(), algorithm=JWT_ALGORITHM)


def get_client_identifier(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
        if client_ip:
            return client_ip
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def enforce_rate_limit(request: Request, action: str, limit: int) -> None:
    identifier = get_client_identifier(request)
    if not rate_limiter.allow(
        key=(action, identifier),
        limit=limit,
        window_seconds=RATE_LIMIT_WINDOW_SECONDS,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED if action == "login" else status.HTTP_400_BAD_REQUEST,
            detail="Too many requests",
        )


def render_page(message: str = "", error: str = "", token: str = "") -> str:
    safe_message = (
        message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if message
        else ""
    )
    safe_error = (
        error.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if error
        else ""
    )
    safe_token = (
        token.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if token
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LoginAPI</title>
    <style>
        * {{
            box-sizing: border-box;
        }}
        body {{
            margin: 0;
            font-family: Arial, sans-serif;
            background: #f4f7fb;
            color: #222;
        }}
        .container {{
            max-width: 960px;
            margin: 40px auto;
            padding: 20px;
        }}
        .header {{
            text-align: center;
            margin-bottom: 30px;
        }}
        .header h1 {{
            margin: 0 0 10px;
        }}
        .grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
        }}
        .card {{
            background: white;
            border-radius: 12px;
            padding: 24px;
            box-shadow: 0 4px 16px rgba(0,0,0,0.08);
        }}
        h2 {{
            margin-top: 0;
        }}
        label {{
            display: block;
            margin: 12px 0 6px;
            font-weight: bold;
        }}
        input {{
            width: 100%;
            padding: 12px;
            border: 1px solid #cfd8e3;
            border-radius: 8px;
            font-size: 14px;
        }}
        button {{
            margin-top: 16px;
            width: 100%;
            padding: 12px;
            border: none;
            border-radius: 8px;
            background: #2563eb;
            color: white;
            font-size: 15px;
            cursor: pointer;
        }}
        button:hover {{
            background: #1d4ed8;
        }}
        .message {{
            margin-bottom: 20px;
            padding: 12px 16px;
            border-radius: 8px;
        }}
        .success {{
            background: #dcfce7;
            color: #166534;
        }}
        .error {{
            background: #fee2e2;
            color: #991b1b;
        }}
        .token {{
            margin-top: 12px;
            padding: 12px;
            background: #f8fafc;
            border-radius: 8px;
            word-break: break-all;
            font-family: monospace;
            font-size: 13px;
        }}
        .footer {{
            margin-top: 24px;
            text-align: center;
            color: #666;
        }}
        @media (max-width: 768px) {{
            .grid {{
                grid-template-columns: 1fr;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>LoginAPI</h1>
            <p>Simple login and registration webapp</p>
        </div>

        {f'<div class="message success">{safe_message}</div>' if safe_message else ''}
        {f'<div class="message error">{safe_error}</div>' if safe_error else ''}

        <div class="grid">
            <div class="card">
                <h2>Login</h2>
                <form id="login-form">
                    <label for="login-email">Email</label>
                    <input id="login-email" name="email" type="email" maxlength="{MAX_EMAIL_LENGTH}" required placeholder="user@example.com">

                    <label for="login-password">Password</label>
                    <input id="login-password" name="password" type="password" maxlength="{MAX_PASSWORD_LENGTH}" required placeholder="Enter password">

                    <button type="submit">Login</button>
                </form>
                <div id="login-result">
                    {f'<div class="token"><strong>Token:</strong><br>{safe_token}</div>' if safe_token else ''}
                </div>
            </div>

            <div class="card">
                <h2>Register</h2>
                <form id="register-form">
                    <label for="register-name">Name</label>
                    <input id="register-name" name="name" type="text" maxlength="{MAX_NAME_LENGTH}" required placeholder="John Doe">

                    <label for="register-email">Email</label>
                    <input id="register-email" name="email" type="email" maxlength="{MAX_EMAIL_LENGTH}" required placeholder="newuser@example.com">

                    <label for="register-password">Password</label>
                    <input id="register-password" name="password" type="password" maxlength="{MAX_PASSWORD_LENGTH}" required placeholder="Create password">

                    <button type="submit">Register</button>
                </form>
                <div id="register-result"></div>
            </div>
        </div>

        <div class="footer">
            <p>API docs available at <a href="/docs">/docs</a></p>
        </div>
    </div>

    <script>
        function escapeHtml(text) {{
            const div = document.createElement('div');
            div.innerText = text;
            return div.innerHTML;
        }}

        document.getElementById('login-form').addEventListener('submit', async function(e) {{
            e.preventDefault();
            const result = document.getElementById('login-result');
            result.innerHTML = '';

            const payload = {{
                email: document.getElementById('login-email').value,
                password: document.getElementById('login-password').value
            }};

            try {{
                const response = await fetch('/login', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(payload)
                }});

                const data = await response.json();

                if (response.ok) {{
                    result.innerHTML =
                        '<div class="message success">' + escapeHtml(data.message) + '</div>' +
                        '<div class="token"><strong>Token:</strong><br>' + escapeHtml(data.token) + '</div>';
                }} else {{
                    result.innerHTML =
                        '<div class="message error">' + escapeHtml(data.message || 'Login failed') + '</div>';
                }}
            }} catch (err) {{
                result.innerHTML =
                    '<div class="message error">An unexpected error occurred.</div>';
            }}
        }});

        document.getElementById('register-form').addEventListener('submit', async function(e) {{
            e.preventDefault();
            const result = document.getElementById('register-result');
            result.innerHTML = '';

            const payload = {{
                name: document.getElementById('register-name').value,
                email: document.getElementById('register-email').value,
                password: document.getElementById('register-password').value
            }};

            try {{
                const response = await fetch('/register', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(payload)
                }});

                const data = await response.json();

                if (response.status === 201) {{
                    result.innerHTML =
                        '<div class="message success">' + escapeHtml(data.message) + '</div>';
                }} else {{
                    result.innerHTML =
                        '<div class="message error">' + escapeHtml(data.message || 'Registration failed') + '</div>';
                }}
            }} catch (err) {{
                result.innerHTML =
                    '<div class="message error">An unexpected error occurred.</div>';
            }}
        }});
    </script>
</body>
</html>"""


@app.on_event("startup")
def on_startup() -> None:
    require_app_secret()
    init_db()


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home() -> HTMLResponse:
    return HTMLResponse(content=render_page())


@app.post(
    "/login",
    response_model=LoginSuccessResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
    },
    tags=["Authentication"],
    summary="User login",
    description="Authenticate a user with email and password.",
    openapi_extra={"security": [{"bearerAuth": []}]},
)
def login(
    request: Request,
    payload: LoginRequest = Body(
        ...,
        openapi_examples={
            "user1": {
                "summary": "Sample user #1",
                "value": {
                    "email": "user1@example.com",
                    "password": "pass1",
                },
            }
        },
    ),
) -> JSONResponse:
    enforce_rate_limit(request, "login", LOGIN_RATE_LIMIT_MAX_ATTEMPTS)

    with closing(get_db_connection()) as conn:
        user = conn.execute(
            "SELECT id, email, password_hash FROM users WHERE email = ?",
            (str(payload.email).lower().strip(),),
        ).fetchone()

    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = create_token(user["id"], user["email"])

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"token": token, "message": "Login successful"},
    )


@app.post(
    "/register",
    response_model=RegisterSuccessResponse,
    status_code=201,
    responses={
        400: {"model": ErrorResponse, "description": "Bad Request"},
    },
    tags=["Authentication"],
    summary="User registration",
    description="Register a new user with email, password, and name.",
    openapi_extra={"security": [{"bearerAuth": []}]},
)
def register(
    request: Request,
    payload: RegisterRequest = Body(
        ...,
        openapi_examples={
            "newUser": {
                "summary": "Sample new user",
                "value": {
                    "email": "newuser@example.com",
                    "password": "newUserPassword",
                    "name": "John Doe",
                },
            }
        },
    ),
) -> JSONResponse:
    enforce_rate_limit(request, "register", REGISTER_RATE_LIMIT_MAX_ATTEMPTS)

    email = str(payload.email).lower().strip()
    name = payload.name.strip()
    password = payload.password

    if not name or not password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )

    password_hash = hash_password(password)

    try:
        with closing(get_db_connection()) as conn:
            conn.execute(
                """
                INSERT INTO users (email, password_hash, name, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    email,
                    password_hash,
                    name,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={"message": "Registration successful"},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return JSONResponse(
        status_code=exc.status_code,
        content={"message": detail},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"message": "Email already in use or invalid data"},
    )


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    schema["openapi"] = "3.0.3"
    schema["components"] = schema.get("components", {})
    schema["components"]["securitySchemes"] = {
        "bearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
    }
    schema["security"] = [{"bearerAuth": []}]
    schema["tags"] = [
        {
            "name": "Authentication",
            "description": "Operations related to user authentication",
        }
    ]

    for path, methods in schema.get("paths", {}).items():
        for method, operation in methods.items():
            if path == "/login":
                operation["responses"] = {
                    "200": {
                        "description": "Successful login",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "token": {
                                            "type": "string",
                                            "example": "jwt-token-abc123",
                                        },
                                        "message": {
                                            "type": "string",
                                            "example": "Login successful",
                                        },
                                    },
                                }
                            }
                        },
                    },
                    "401": {
                        "description": "Unauthorized",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "message": {
                                            "type": "string",
                                            "example": "Invalid email or password",
                                        }
                                    },
                                }
                            }
                        },
                    },
                }
                operation["security"] = [{"bearerAuth": []}]
            elif path == "/register":
                operation["responses"] = {
                    "201": {
                        "description": "Successful registration",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "message": {
                                            "type": "string",
                                            "example": "Registration successful",
                                        }
                                    },
                                }
                            }
                        },
                    },
                    "400": {
                        "description": "Bad Request",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "message": {
                                            "type": "string",
                                            "example": "Email already in use or invalid data",
                                        }
                                    },
                                }
                            }
                        },
                    },
                }
                operation["security"] = [{"bearerAuth": []}]

    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi


if __name__ == "__main__":
    require_app_secret()
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)