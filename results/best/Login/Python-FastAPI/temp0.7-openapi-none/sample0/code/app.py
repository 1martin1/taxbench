# проходит все тесты

import os
import sqlite3
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, status, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, EmailStr, Field
from passlib.context import CryptContext
import jwt  # pyjwt

# Configuration
APP_TITLE = "LoginAPI"
APP_DESCRIPTION = "A simple webapp API for login and registration functionality."
APP_VERSION = "1.0.11"
DB_PATH = "db.sqlite3"
JWT_ALGORITHM = "HS256"
JWT_EXP_MINUTES = 60  # token expiry in minutes

# Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(APP_TITLE)

# Read secret
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    APP_SECRET = "dev-secret-change-this"
    logger.warning("APP_SECRET not set; using a default development secret. Set APP_SECRET for production.")

# Password hashing
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

# FastAPI app
app = FastAPI(title=APP_TITLE, description=APP_DESCRIPTION, version=APP_VERSION)


# Database utilities
def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            )
            """
        )
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)")
        conn.commit()
    finally:
        conn.close()


@app.on_event("startup")
def on_startup():
    init_db()


# Pydantic models
class LoginRequest(BaseModel):
    email: EmailStr = Field(example="user@example.com")
    password: str = Field(example="mySecretPassword")


class LoginSuccessResponse(BaseModel):
    token: str = Field(example="jwt-token-abc123")
    message: str = Field(example="Login successful")


class RegisterRequest(BaseModel):
    email: EmailStr = Field(example="newuser@example.com")
    password: str = Field(example="newUserPassword")
    name: str = Field(example="John Doe")


class RegistrationSuccessResponse(BaseModel):
    message: str = Field(example="Registration successful")


class ErrorResponse(BaseModel):
    message: str


# HTML UI (simple page with login and register)
HTML_PAGE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>LoginAPI Demo</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    :root {
      --bg: #0f172a;
      --panel: #111827;
      --border: #1f2937;
      --text: #e5e7eb;
      --muted: #9ca3af;
      --accent: #3b82f6;
      --success: #10b981;
      --error: #ef4444;
    }
    body {
      background: linear-gradient(180deg, #0b1220, #0f172a);
      color: var(--text);
      font-family: system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, "Helvetica Neue", "Noto Sans", Arial, "Apple Color Emoji", "Segoe UI Emoji";
      margin: 0;
      padding: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
    }
    .container {
      display: grid;
      gap: 24px;
      width: 100%;
      max-width: 960px;
      padding: 24px;
      box-sizing: border-box;
    }
    header {
      text-align: center;
    }
    header h1 {
      margin: 0 0 8px;
      font-size: 28px;
    }
    header p {
      margin: 0;
      color: var(--muted);
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 24px;
    }
    .card {
      background: rgba(17, 24, 39, 0.8);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 20px;
      backdrop-filter: blur(6px);
    }
    .card h2 {
      margin: 0 0 16px;
      font-size: 18px;
    }
    label {
      display: block;
      margin: 12px 0 6px;
      color: var(--muted);
      font-size: 14px;
    }
    input {
      width: 100%;
      padding: 10px 12px;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: #0b1020;
      color: var(--text);
      box-sizing: border-box;
      outline: none;
    }
    input:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 2px rgba(59,130,246,0.25);
    }
    button {
      margin-top: 16px;
      width: 100%;
      padding: 10px 12px;
      border: 1px solid var(--accent);
      background: var(--accent);
      color: white;
      border-radius: 8px;
      font-weight: 600;
      cursor: pointer;
    }
    button:hover { filter: brightness(1.05); }
    .message {
      margin-top: 12px;
      padding: 10px 12px;
      border-radius: 8px;
      font-size: 14px;
      display: none;
    }
    .message.success { background: rgba(16,185,129,0.15); border: 1px solid rgba(16,185,129,0.35); color: #bbf7d0; display:block; }
    .message.error { background: rgba(239,68,68,0.15); border: 1px solid rgba(239,68,68,0.35); color: #fecaca; display:block; }
    code.token {
      display: block;
      word-break: break-all;
      background: #0b1020;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 8px;
      margin-top: 8px;
      color: #d1fae5;
    }
    footer {
      text-align: center;
      color: var(--muted);
      font-size: 12px;
      margin-top: 8px;
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>LoginAPI Demo</h1>
      <p>A simple demo for registration and login using FastAPI and SQLite.</p>
    </header>

    <div class="grid">
      <section class="card">
        <h2>Register</h2>
        <label for="reg-name">Name</label>
        <input id="reg-name" placeholder="John Doe" autocomplete="name" />
        <label for="reg-email">Email</label>
        <input id="reg-email" placeholder="newuser@example.com" autocomplete="email" />
        <label for="reg-password">Password</label>
        <input id="reg-password" type="password" placeholder="••••••••" autocomplete="new-password" />
        <button id="btn-register">Create Account</button>
        <div id="reg-message" class="message"></div>
      </section>

      <section class="card">
        <h2>Login</h2>
        <label for="login-email">Email</label>
        <input id="login-email" placeholder="user@example.com" autocomplete="email" />
        <label for="login-password">Password</label>
        <input id="login-password" type="password" placeholder="••••••••" autocomplete="current-password" />
        <button id="btn-login">Login</button>
        <div id="login-message" class="message"></div>
        <code id="token" class="token" style="display:none;"></code>
      </section>
    </div>

    <footer>
      OpenAPI available at /openapi.json • Interactive docs at /docs
    </footer>
  </div>

  <script>
    async function api(path, data) {
      const res = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data || {})
      });
      const text = await res.text();
      let json;
      try { json = JSON.parse(text); } catch { json = { message: text }; }
      return { ok: res.ok, status: res.status, data: json };
    }

    function setMessage(el, ok, msg) {
      el.classList.remove("success", "error");
      el.classList.add(ok ? "success" : "error");
      el.textContent = msg;
      el.style.display = "block";
    }

    document.getElementById("btn-register").addEventListener("click", async () => {
      const name = document.getElementById("reg-name").value.trim();
      const email = document.getElementById("reg-email").value.trim();
      const password = document.getElementById("reg-password").value;
      const msg = document.getElementById("reg-message");
      msg.style.display = "none";

      const r = await api("/register", { name, email, password });
      setMessage(msg, r.ok, r.data && r.data.message ? r.data.message : (r.ok ? "Registered" : "Registration failed"));
    });

    document.getElementById("btn-login").addEventListener("click", async () => {
      const email = document.getElementById("login-email").value.trim();
      const password = document.getElementById("login-password").value;
      const msg = document.getElementById("login-message");
      const tokenEl = document.getElementById("token");
      msg.style.display = "none";
      tokenEl.style.display = "none";
      tokenEl.textContent = "";

      const r = await api("/login", { email, password });
      if (r.ok) {
        setMessage(msg, true, r.data.message || "Login successful");
        const token = r.data.token || "";
        tokenEl.textContent = token;
        tokenEl.style.display = "block";
        try { localStorage.setItem("token", token); } catch (_) {}
      } else {
        setMessage(msg, false, r.data && r.data.message ? r.data.message : "Invalid email or password");
      }
    });
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> HTMLResponse:
    return HTMLResponse(content=HTML_PAGE)


# Helper functions
def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(plain, hashed)
    except Exception:
        return False


def generate_jwt(sub: str, user_id: int, name: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "uid": user_id,
        "name": name,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=JWT_EXP_MINUTES)).timestamp()),
    }
    token = jwt.encode(payload, APP_SECRET, algorithm=JWT_ALGORITHM)
    # pyjwt returns str for PyJWT>=2
    return token


# API endpoints as per schema

@app.post(
    "/login",
    response_model=LoginSuccessResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
    },
    summary="User login",
    tags=["Authentication"],
)
def login(body: LoginRequest):
    email = body.email
    password = body.password

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, email, password_hash, name FROM users WHERE email = ?", (str(email).lower(),))
        row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    if not verify_password(password, row["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    token = generate_jwt(sub=row["email"], user_id=row["id"], name=row["name"])
    return {"token": token, "message": "Login successful"}


@app.post(
    "/register",
    response_model=RegistrationSuccessResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Bad Request"},
    },
    summary="User registration",
    tags=["Authentication"],
)
def register(body: RegisterRequest):
    email = str(body.email).lower().strip()
    password = body.password
    name = body.name.strip()

    if not email or not password or not name:
        # Shouldn't happen due to validation, but just in case
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use or invalid data")

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM users WHERE email = ?", (email,))
        exists = cur.fetchone() is not None
        if exists:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use or invalid data")

        password_hash = hash_password(password)
        cur.execute(
            "INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)",
            (email, password_hash, name),
        )
        conn.commit()
    finally:
        conn.close()

    return {"message": "Registration successful"}


# Custom exception handler to shape error messages as in schema
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # Align error body with {"message": "..."}
    if exc.status_code in (status.HTTP_400_BAD_REQUEST, status.HTTP_401_UNAUTHORIZED):
        return JSONResponse(status_code=exc.status_code, content={"message": exc.detail or "Error"})
    # Fallback to FastAPI default for other codes
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


# Override OpenAPI schema to correspond exactly to the provided one
def custom_openapi():
    schema = {
        "openapi": "3.0.3",
        "info": {
            "title": "LoginAPI",
            "description": "A simple webapp API for login and registration functionality.",
            "version": "1.0.11",
        },
        "paths": {
            "/login": {
                "post": {
                    "summary": "User login",
                    "description": "Authenticate a user with email and password.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "email": {"type": "string", "format": "email", "example": "user@example.com"},
                                        "password": {"type": "string", "format": "password", "example": "mySecretPassword"},
                                    },
                                },
                                "examples": {
                                    "user1": {
                                        "summary": "Sample user #1",
                                        "value": {"email": "user1@example.com", "password": "pass1"},
                                    }
                                },
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Successful login",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "token": {"type": "string", "example": "jwt-token-abc123"},
                                            "message": {"type": "string", "example": "Login successful"},
                                        },
                                    }
                                }
                            },
                        },
                        "401": {
                            "description": "Unauthorized",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"message": {"type": "string", "example": "Invalid email or password"}}}
                                }
                            },
                        },
                    },
                }
            },
            "/register": {
                "post": {
                    "summary": "User registration",
                    "description": "Register a new user with email, password, and name.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "email": {"type": "string", "format": "email", "example": "newuser@example.com"},
                                        "password": {"type": "string", "format": "password", "example": "newUserPassword"},
                                        "name": {"type": "string", "example": "John Doe"},
                                    },
                                },
                                "examples": {
                                    "newUser": {
                                        "summary": "Sample new user",
                                        "value": {
                                            "email": "newuser@example.com",
                                            "password": "newUserPassword",
                                            "name": "John Doe",
                                        },
                                    }
                                },
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "Successful registration",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"message": {"type": "string", "example": "Registration successful"}}}
                                }
                            },
                        },
                        "400": {
                            "description": "Bad Request",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"message": {"type": "string", "example": "Email already in use or invalid data"}}}
                                }
                            },
                        },
                    },
                }
            },
        },
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
            }
        },
        "security": [{"bearerAuth": []}],
        "tags": [
            {"name": "Authentication", "description": "Operations related to user authentication"}
        ],
    }
    return schema


app.openapi = custom_openapi


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)