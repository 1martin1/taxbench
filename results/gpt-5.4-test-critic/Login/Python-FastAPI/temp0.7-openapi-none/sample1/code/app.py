import os
import sqlite3
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from jose import jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field
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
)


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
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


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_token(subject: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=TOKEN_EXPIRE_MINUTES)).timestamp()),
        "jti": secrets.token_hex(16),
    }
    return jwt.encode(payload, APP_SECRET, algorithm=JWT_ALGORITHM)


def get_user_by_email(email: str) -> Optional[sqlite3.Row]:
    with get_db() as conn:
        cur = conn.execute(
            "SELECT id, email, password_hash, name, created_at FROM users WHERE email = ?",
            (email.lower(),),
        )
        return cur.fetchone()


@app.on_event("startup")
def startup_event():
    init_db()


@app.get("/", response_class=HTMLResponse)
def home():
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
            background: white;
            border-radius: 12px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.08);
            padding: 24px;
        }
        h1 {
            margin-top: 0;
            text-align: center;
        }
        .tabs {
            display: flex;
            gap: 8px;
            margin-bottom: 20px;
        }
        .tab-btn {
            flex: 1;
            border: none;
            background: #e9eef8;
            padding: 12px;
            border-radius: 8px;
            cursor: pointer;
            font-weight: bold;
        }
        .tab-btn.active {
            background: #2563eb;
            color: white;
        }
        form {
            display: none;
        }
        form.active {
            display: block;
        }
        label {
            display: block;
            margin-bottom: 6px;
            margin-top: 12px;
            font-size: 14px;
            font-weight: 600;
        }
        input {
            width: 100%;
            box-sizing: border-box;
            padding: 10px 12px;
            border: 1px solid #ccd5e1;
            border-radius: 8px;
            font-size: 14px;
        }
        button.submit-btn {
            width: 100%;
            margin-top: 18px;
            border: none;
            background: #2563eb;
            color: white;
            padding: 12px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 15px;
            font-weight: bold;
        }
        .result {
            margin-top: 18px;
            padding: 12px;
            border-radius: 8px;
            background: #f6f8fc;
            white-space: pre-wrap;
            word-break: break-word;
            font-size: 13px;
        }
        .footer {
            text-align: center;
            color: #666;
            margin-top: 16px;
            font-size: 12px;
        }
        code {
            background: #eef2ff;
            padding: 2px 5px;
            border-radius: 4px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>LoginAPI</h1>

        <div class="tabs">
            <button class="tab-btn active" onclick="showTab('login')">Login</button>
            <button class="tab-btn" onclick="showTab('register')">Register</button>
        </div>

        <form id="login-form" class="active">
            <label for="login-email">Email</label>
            <input id="login-email" type="email" placeholder="user@example.com" required>

            <label for="login-password">Password</label>
            <input id="login-password" type="password" placeholder="Password" required>

            <button class="submit-btn" type="submit">Login</button>
        </form>

        <form id="register-form">
            <label for="register-name">Name</label>
            <input id="register-name" type="text" placeholder="John Doe" required>

            <label for="register-email">Email</label>
            <input id="register-email" type="email" placeholder="newuser@example.com" required>

            <label for="register-password">Password</label>
            <input id="register-password" type="password" placeholder="Password" required>

            <button class="submit-btn" type="submit">Register</button>
        </form>

        <div id="result" class="result">Ready.</div>
        <div class="footer">API endpoints: <code>POST /login</code> and <code>POST /register</code></div>
    </div>

    <script>
        function showTab(tab) {
            const loginBtn = document.querySelectorAll('.tab-btn')[0];
            const registerBtn = document.querySelectorAll('.tab-btn')[1];
            const loginForm = document.getElementById('login-form');
            const registerForm = document.getElementById('register-form');

            if (tab === 'login') {
                loginBtn.classList.add('active');
                registerBtn.classList.remove('active');
                loginForm.classList.add('active');
                registerForm.classList.remove('active');
            } else {
                registerBtn.classList.add('active');
                loginBtn.classList.remove('active');
                registerForm.classList.add('active');
                loginForm.classList.remove('active');
            }
            document.getElementById('result').textContent = 'Ready.';
        }

        async function handleRequest(url, payload) {
            const result = document.getElementById('result');
            result.textContent = 'Loading...';

            try {
                const response = await fetch(url, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });

                const data = await response.json();
                result.textContent = 'Status: ' + response.status + '\\n\\n' + JSON.stringify(data, null, 2);
            } catch (error) {
                result.textContent = 'Request failed: ' + error;
            }
        }

        document.getElementById('login-form').addEventListener('submit', async function(e) {
            e.preventDefault();
            await handleRequest('/login', {
                email: document.getElementById('login-email').value,
                password: document.getElementById('login-password').value
            });
        });

        document.getElementById('register-form').addEventListener('submit', async function(e) {
            e.preventDefault();
            await handleRequest('/register', {
                name: document.getElementById('register-name').value,
                email: document.getElementById('register-email').value,
                password: document.getElementById('register-password').value
            });
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
                    "example": {
                        "message": "Invalid email or password",
                    }
                }
            },
        },
    },
)
def login(payload: LoginRequest):
    user = get_user_by_email(payload.email)
    if not user or not verify_password(payload.password, user["password_hash"]):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"message": "Invalid email or password"},
        )

    token = create_token(user["email"])
    return {"token": token, "message": "Login successful"}


@app.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=MessageResponse,
    tags=["Authentication"],
    responses={
        201: {
            "description": "Successful registration",
            "content": {
                "application/json": {
                    "example": {
                        "message": "Registration successful",
                    }
                }
            },
        },
        400: {
            "description": "Bad Request",
            "content": {
                "application/json": {
                    "example": {
                        "message": "Email already in use or invalid data",
                    }
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

    if get_user_by_email(email):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Email already in use or invalid data"},
        )

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO users (email, password_hash, name, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                email,
                hash_password(password),
                name,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    return {"message": "Registration successful"}


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)