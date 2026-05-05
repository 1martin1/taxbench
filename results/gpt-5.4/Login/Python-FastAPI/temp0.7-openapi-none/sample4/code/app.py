import os
import sqlite3
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Form, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jose import jwt, JWTError
from pydantic import BaseModel, EmailStr, Field
import uvicorn


DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET", "change-me-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = 60 * 24


app = FastAPI(
    title="LoginAPI",
    description="A simple webapp API for login and registration functionality.",
    version="1.0.11",
)


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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
        100_000,
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
        100_000,
    ).hex()
    return hmac.compare_digest(digest, expected_hash)


def create_access_token(subject: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=JWT_EXPIRE_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, APP_SECRET, algorithm=JWT_ALGORITHM)


def get_user_by_email(email: str) -> Optional[sqlite3.Row]:
    conn = get_db_connection()
    try:
        cur = conn.execute(
            "SELECT id, email, name, password_hash, created_at FROM users WHERE email = ?",
            (email.lower(),),
        )
        return cur.fetchone()
    finally:
        conn.close()


def create_user(email: str, password: str, name: str) -> bool:
    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO users (email, name, password_hash, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                email.lower(),
                name,
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
    password: str = Field(..., min_length=1, examples=["newUserPassword"])
    name: str = Field(..., min_length=1, examples=["John Doe"])


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home(request: Request) -> HTMLResponse:
    token = request.cookies.get("auth_token")
    user_email = None

    if token:
        try:
            payload = jwt.decode(token, APP_SECRET, algorithms=[JWT_ALGORITHM])
            user_email = payload.get("sub")
        except JWTError:
            user_email = None

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <title>Login Webapp</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                background: #f4f7fb;
                margin: 0;
                padding: 0;
            }}
            .container {{
                max-width: 420px;
                margin: 60px auto;
                background: white;
                padding: 24px;
                border-radius: 12px;
                box-shadow: 0 8px 24px rgba(0,0,0,0.08);
            }}
            h1 {{
                margin-top: 0;
                font-size: 28px;
                text-align: center;
            }}
            h2 {{
                font-size: 20px;
                margin-bottom: 12px;
            }}
            form {{
                display: flex;
                flex-direction: column;
                gap: 12px;
                margin-bottom: 24px;
            }}
            input {{
                padding: 12px;
                border: 1px solid #d0d7e2;
                border-radius: 8px;
                font-size: 14px;
            }}
            button {{
                padding: 12px;
                border: none;
                border-radius: 8px;
                background: #2563eb;
                color: white;
                font-size: 14px;
                cursor: pointer;
            }}
            button:hover {{
                background: #1d4ed8;
            }}
            .muted {{
                color: #555;
                font-size: 14px;
                text-align: center;
                margin-bottom: 16px;
            }}
            .success {{
                background: #ecfdf5;
                color: #065f46;
                border: 1px solid #a7f3d0;
                padding: 12px;
                border-radius: 8px;
                margin-bottom: 16px;
            }}
            .section {{
                border-top: 1px solid #e5e7eb;
                padding-top: 20px;
                margin-top: 20px;
            }}
            .logout {{
                display: inline-block;
                text-decoration: none;
                text-align: center;
                width: 100%;
                padding: 12px;
                border-radius: 8px;
                background: #dc2626;
                color: white;
            }}
            .logout:hover {{
                background: #b91c1c;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Login Webapp</h1>
            {"<div class='success'>Logged in as: " + user_email + "</div>" if user_email else "<div class='muted'>Please log in or register.</div>"}

            <h2>Login</h2>
            <form method="post" action="/web/login">
                <input type="email" name="email" placeholder="Email" required />
                <input type="password" name="password" placeholder="Password" required />
                <button type="submit">Login</button>
            </form>

            <div class="section">
                <h2>Register</h2>
                <form method="post" action="/web/register">
                    <input type="text" name="name" placeholder="Full name" required />
                    <input type="email" name="email" placeholder="Email" required />
                    <input type="password" name="password" placeholder="Password" required />
                    <button type="submit">Register</button>
                </form>
            </div>

            <div class="section">
                <a class="logout" href="/logout">Logout</a>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@app.post(
    "/login",
    response_model=LoginSuccessResponse,
    tags=["Authentication"],
    responses={
        200: {"description": "Successful login"},
        401: {"description": "Unauthorized", "model": MessageResponse},
    },
)
def login(payload: LoginRequest):
    user = get_user_by_email(payload.email)
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = create_access_token(user["email"])
    return {"token": token, "message": "Login successful"}


@app.post(
    "/register",
    status_code=201,
    response_model=MessageResponse,
    tags=["Authentication"],
    responses={
        201: {"description": "Successful registration"},
        400: {"description": "Bad Request", "model": MessageResponse},
    },
)
def register(payload: RegisterRequest):
    if not payload.name.strip() or not payload.password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )

    created = create_user(payload.email, payload.password, payload.name.strip())
    if not created:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )

    return {"message": "Registration successful"}


@app.post("/web/login", include_in_schema=False)
def web_login(email: str = Form(...), password: str = Form(...)):
    user = get_user_by_email(email)
    if not user or not verify_password(password, user["password_hash"]):
        return HTMLResponse(
            content="""
            <html><body style="font-family:Arial;padding:40px;">
            <h2>Login failed</h2>
            <p>Invalid email or password.</p>
            <p><a href="/">Back</a></p>
            </body></html>
            """,
            status_code=401,
        )

    token = create_access_token(user["email"])
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key="auth_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=JWT_EXPIRE_MINUTES * 60,
    )
    return response


@app.post("/web/register", include_in_schema=False)
def web_register(name: str = Form(...), email: str = Form(...), password: str = Form(...)):
    if not name.strip() or not password or "@" not in email:
        return HTMLResponse(
            content="""
            <html><body style="font-family:Arial;padding:40px;">
            <h2>Registration failed</h2>
            <p>Email already in use or invalid data.</p>
            <p><a href="/">Back</a></p>
            </body></html>
            """,
            status_code=400,
        )

    created = create_user(email, password, name.strip())
    if not created:
        return HTMLResponse(
            content="""
            <html><body style="font-family:Arial;padding:40px;">
            <h2>Registration failed</h2>
            <p>Email already in use or invalid data.</p>
            <p><a href="/">Back</a></p>
            </body></html>
            """,
            status_code=400,
        )

    return HTMLResponse(
        content="""
        <html><body style="font-family:Arial;padding:40px;">
        <h2>Registration successful</h2>
        <p>Your account has been created.</p>
        <p><a href="/">Go to login page</a></p>
        </body></html>
        """,
        status_code=201,
    )


@app.get("/logout", include_in_schema=False)
def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("auth_token")
    return response


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    if request.url.path in ["/login", "/register"]:
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return JSONResponse(
            status_code=exc.status_code,
            content={"message": detail},
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"message": exc.detail if isinstance(exc.detail, str) else "Request failed"},
    )


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)