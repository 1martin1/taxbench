import os
import sqlite3
import secrets
import hashlib
import hmac
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, EmailStr, Field
from jose import jwt, JWTError
import uvicorn


DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET", "development-secret-change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24


app = FastAPI(
    title="LoginAPI",
    description="A simple webapp API for login and registration functionality.",
    version="1.0.11",
)


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
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    iterations = 200000
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, iterations_str, salt, expected_hash = stored_hash.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iterations_str)
        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            iterations,
        ).hex()
        return hmac.compare_digest(candidate, expected_hash)
    except Exception:
        return False


def create_token(user_id: int, email: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=JWT_EXPIRE_HOURS)).timestamp()),
    }
    return jwt.encode(payload, APP_SECRET, algorithm=JWT_ALGORITHM)


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


@app.on_event("startup")
def startup_event() -> None:
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
        :root {
            color-scheme: light dark;
        }
        body {
            font-family: Arial, sans-serif;
            background: #f4f7fb;
            color: #1f2937;
            margin: 0;
            padding: 0;
        }
        .container {
            max-width: 420px;
            margin: 48px auto;
            background: white;
            border-radius: 12px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.08);
            overflow: hidden;
        }
        .tabs {
            display: flex;
        }
        .tab {
            flex: 1;
            padding: 16px;
            border: none;
            cursor: pointer;
            background: #e5e7eb;
            font-size: 16px;
            font-weight: bold;
        }
        .tab.active {
            background: white;
            color: #2563eb;
        }
        .panel {
            padding: 24px;
            display: none;
        }
        .panel.active {
            display: block;
        }
        h1 {
            font-size: 22px;
            margin-top: 0;
            margin-bottom: 20px;
        }
        label {
            display: block;
            margin-bottom: 8px;
            font-weight: 600;
        }
        input {
            width: 100%;
            box-sizing: border-box;
            padding: 12px;
            margin-bottom: 16px;
            border: 1px solid #cbd5e1;
            border-radius: 8px;
            font-size: 14px;
        }
        button.submit {
            width: 100%;
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
            background: #dcfce7;
            color: #166534;
        }
        .error {
            background: #fee2e2;
            color: #991b1b;
        }
        .footer {
            text-align: center;
            margin-top: 18px;
            font-size: 13px;
            color: #6b7280;
        }
        @media (prefers-color-scheme: dark) {
            body {
                background: #111827;
                color: #f9fafb;
            }
            .container {
                background: #1f2937;
                box-shadow: 0 10px 30px rgba(0,0,0,0.35);
            }
            .tab {
                background: #374151;
                color: #f9fafb;
            }
            .tab.active {
                background: #1f2937;
                color: #60a5fa;
            }
            input {
                background: #111827;
                color: #f9fafb;
                border-color: #4b5563;
            }
            .success {
                background: #14532d;
                color: #dcfce7;
            }
            .error {
                background: #7f1d1d;
                color: #fee2e2;
            }
            .footer {
                color: #9ca3af;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="tabs">
            <button class="tab active" id="loginTab" onclick="showTab('login')">Login</button>
            <button class="tab" id="registerTab" onclick="showTab('register')">Register</button>
        </div>

        <div class="panel active" id="loginPanel">
            <h1>User Login</h1>
            <form id="loginForm">
                <label for="loginEmail">Email</label>
                <input id="loginEmail" name="email" type="email" required placeholder="user@example.com">

                <label for="loginPassword">Password</label>
                <input id="loginPassword" name="password" type="password" required placeholder="mySecretPassword">

                <button class="submit" type="submit">Login</button>
            </form>
            <div id="loginResult"></div>
        </div>

        <div class="panel" id="registerPanel">
            <h1>User Registration</h1>
            <form id="registerForm">
                <label for="registerName">Name</label>
                <input id="registerName" name="name" type="text" required placeholder="John Doe">

                <label for="registerEmail">Email</label>
                <input id="registerEmail" name="email" type="email" required placeholder="newuser@example.com">

                <label for="registerPassword">Password</label>
                <input id="registerPassword" name="password" type="password" required placeholder="newUserPassword">

                <button class="submit" type="submit">Register</button>
            </form>
            <div id="registerResult"></div>
        </div>
    </div>

    <div class="footer">
        API docs available at <a href="/docs">/docs</a>
    </div>

    <script>
        function showTab(tab) {
            const loginTab = document.getElementById('loginTab');
            const registerTab = document.getElementById('registerTab');
            const loginPanel = document.getElementById('loginPanel');
            const registerPanel = document.getElementById('registerPanel');

            if (tab === 'login') {
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

        function renderResult(elementId, data, ok) {
            const el = document.getElementById(elementId);
            el.className = 'result ' + (ok ? 'success' : 'error');
            el.textContent = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
        }

        document.getElementById('loginForm').addEventListener('submit', async function (e) {
            e.preventDefault();
            const payload = {
                email: document.getElementById('loginEmail').value,
                password: document.getElementById('loginPassword').value
            };

            try {
                const response = await fetch('/login', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const data = await response.json();
                renderResult('loginResult', data, response.ok);
            } catch (err) {
                renderResult('loginResult', 'Request failed', false);
            }
        });

        document.getElementById('registerForm').addEventListener('submit', async function (e) {
            e.preventDefault();
            const payload = {
                name: document.getElementById('registerName').value,
                email: document.getElementById('registerEmail').value,
                password: document.getElementById('registerPassword').value
            };

            try {
                const response = await fetch('/register', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const data = await response.json();
                renderResult('registerResult', data, response.ok);
            } catch (err) {
                renderResult('registerResult', 'Request failed', false);
            }
        });
    </script>
</body>
</html>
    """


@app.post(
    "/login",
    response_model=LoginSuccessResponse,
    tags=["Authentication"],
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
def login(payload: LoginRequest):
    with closing(get_db_connection()) as conn:
        row = conn.execute(
            "SELECT id, email, password_hash FROM users WHERE email = ?",
            (payload.email.lower(),),
        ).fetchone()

    if row is None or not verify_password(payload.password, row["password_hash"]):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"message": "Invalid email or password"},
        )

    token = create_token(row["id"], row["email"])
    return {"token": token, "message": "Login successful"}


@app.post(
    "/register",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Authentication"],
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
def register(payload: RegisterRequest):
    email = payload.email.lower().strip()
    name = payload.name.strip()
    password = payload.password

    if not name or not password:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Email already in use or invalid data"},
        )

    password_hash = hash_password(password)

    try:
        with closing(get_db_connection()) as conn:
            conn.execute(
                """
                INSERT INTO users (email, name, password_hash, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    email,
                    name,
                    password_hash,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Email already in use or invalid data"},
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={"message": "Registration successful"},
    )


@app.get("/me", include_in_schema=False)
def me(request: Request):
    auth_header: Optional[str] = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = auth_header.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=[JWT_ALGORITHM])
        return {
            "user_id": payload.get("sub"),
            "email": payload.get("email"),
        }
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)