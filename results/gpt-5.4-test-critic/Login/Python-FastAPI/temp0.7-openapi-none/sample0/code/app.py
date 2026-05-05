import os
import sqlite3
import secrets
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from jose import jwt
from pydantic import BaseModel, EmailStr, Field
from passlib.context import CryptContext
import uvicorn


DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET", "development-secret-change-me")
JWT_ALGORITHM = "HS256"
TOKEN_EXPIRE_MINUTES = 60 * 24

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

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


class LoginRequest(BaseModel):
    email: EmailStr = Field(..., examples=["user@example.com"])
    password: str = Field(..., examples=["mySecretPassword"])


class LoginSuccessResponse(BaseModel):
    token: str
    message: str


class MessageResponse(BaseModel):
    message: str


class RegisterRequest(BaseModel):
    email: EmailStr = Field(..., examples=["newuser@example.com"])
    password: str = Field(..., examples=["newUserPassword"])
    name: str = Field(..., examples=["John Doe"])


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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


def create_token(email: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=TOKEN_EXPIRE_MINUTES)).timestamp()),
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(payload, APP_SECRET, algorithm=JWT_ALGORITHM)


def get_user_by_email(email: str) -> Optional[sqlite3.Row]:
    with closing(get_db_connection()) as conn:
        cur = conn.execute(
            "SELECT id, email, password_hash, name, created_at FROM users WHERE email = ?",
            (email.lower(),),
        )
        return cur.fetchone()


def create_user(email: str, password: str, name: str) -> None:
    password_hash = pwd_context.hash(password)
    with closing(get_db_connection()) as conn:
        conn.execute(
            """
            INSERT INTO users (email, password_hash, name, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                email.lower(),
                password_hash,
                name.strip(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index() -> HTMLResponse:
    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <title>LoginAPI</title>
        <style>
            :root {
                color-scheme: light dark;
                --bg: #0f172a;
                --card: #111827;
                --muted: #94a3b8;
                --text: #e5e7eb;
                --primary: #2563eb;
                --primary-hover: #1d4ed8;
                --border: #334155;
                --success: #16a34a;
                --error: #dc2626;
                --input-bg: #0b1220;
            }
            * { box-sizing: border-box; }
            body {
                margin: 0;
                font-family: Arial, sans-serif;
                background: linear-gradient(135deg, #0f172a, #1e293b);
                color: var(--text);
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 24px;
            }
            .container {
                width: 100%;
                max-width: 960px;
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
                gap: 24px;
            }
            .card {
                background: rgba(17, 24, 39, 0.95);
                border: 1px solid var(--border);
                border-radius: 16px;
                padding: 24px;
                box-shadow: 0 20px 40px rgba(0,0,0,0.25);
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
                background: var(--input-bg);
                color: var(--text);
                outline: none;
            }
            input:focus {
                border-color: var(--primary);
            }
            button {
                margin-top: 16px;
                width: 100%;
                padding: 12px;
                border: none;
                border-radius: 10px;
                background: var(--primary);
                color: white;
                font-size: 16px;
                font-weight: bold;
                cursor: pointer;
            }
            button:hover {
                background: var(--primary-hover);
            }
            .result {
                margin-top: 16px;
                padding: 12px;
                border-radius: 10px;
                white-space: pre-wrap;
                word-break: break-word;
                border: 1px solid var(--border);
                background: #020617;
            }
            .success { border-color: rgba(22, 163, 74, 0.5); }
            .error { border-color: rgba(220, 38, 38, 0.5); }
            .small {
                font-size: 14px;
                color: var(--muted);
            }
            code {
                background: #020617;
                padding: 2px 6px;
                border-radius: 6px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="card">
                <h1>LoginAPI</h1>
                <p>Simple login and registration webapp backed by FastAPI and SQLite.</p>
                <p class="small">API docs: <a href="/docs" style="color:#93c5fd;">/docs</a></p>

                <h2>Login</h2>
                <form id="loginForm">
                    <label for="loginEmail">Email</label>
                    <input id="loginEmail" name="email" type="email" placeholder="user@example.com" required />

                    <label for="loginPassword">Password</label>
                    <input id="loginPassword" name="password" type="password" placeholder="Enter password" required />

                    <button type="submit">Login</button>
                </form>
                <div id="loginResult" class="result" style="display:none;"></div>
            </div>

            <div class="card">
                <h2>Register</h2>
                <form id="registerForm">
                    <label for="registerName">Name</label>
                    <input id="registerName" name="name" type="text" placeholder="John Doe" required />

                    <label for="registerEmail">Email</label>
                    <input id="registerEmail" name="email" type="email" placeholder="newuser@example.com" required />

                    <label for="registerPassword">Password</label>
                    <input id="registerPassword" name="password" type="password" placeholder="Create password" required />

                    <button type="submit">Register</button>
                </form>
                <div id="registerResult" class="result" style="display:none;"></div>
            </div>
        </div>

        <script>
            function showResult(elementId, data, ok) {
                const el = document.getElementById(elementId);
                el.style.display = 'block';
                el.className = 'result ' + (ok ? 'success' : 'error');
                el.textContent = JSON.stringify(data, null, 2);
            }

            document.getElementById('loginForm').addEventListener('submit', async (e) => {
                e.preventDefault();
                const payload = {
                    email: document.getElementById('loginEmail').value,
                    password: document.getElementById('loginPassword').value
                };

                try {
                    const res = await fetch('/login', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    const data = await res.json();
                    showResult('loginResult', data, res.ok);
                } catch (err) {
                    showResult('loginResult', { message: 'Request failed' }, false);
                }
            });

            document.getElementById('registerForm').addEventListener('submit', async (e) => {
                e.preventDefault();
                const payload = {
                    name: document.getElementById('registerName').value,
                    email: document.getElementById('registerEmail').value,
                    password: document.getElementById('registerPassword').value
                };

                try {
                    const res = await fetch('/register', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    const data = await res.json();
                    showResult('registerResult', data, res.ok);
                } catch (err) {
                    showResult('registerResult', { message: 'Request failed' }, false);
                }
            });
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


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
            "model": MessageResponse,
            "content": {
                "application/json": {
                    "example": {"message": "Invalid email or password"}
                }
            },
        },
    },
)
async def login(payload: LoginRequest) -> JSONResponse:
    user = get_user_by_email(payload.email)
    if not user or not pwd_context.verify(payload.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = create_token(user["email"])
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"token": token, "message": "Login successful"},
    )


@app.post(
    "/register",
    tags=["Authentication"],
    status_code=201,
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
            "model": MessageResponse,
            "content": {
                "application/json": {
                    "example": {"message": "Email already in use or invalid data"}
                }
            },
        },
    },
)
async def register(payload: RegisterRequest) -> JSONResponse:
    name = payload.name.strip()
    password = payload.password

    if not name or len(password) < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )

    existing = get_user_by_email(payload.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )

    try:
        create_user(payload.email, password, name)
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
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, str):
        return JSONResponse(status_code=exc.status_code, content={"message": exc.detail})
    return JSONResponse(status_code=exc.status_code, content={"message": "Request failed"})


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)