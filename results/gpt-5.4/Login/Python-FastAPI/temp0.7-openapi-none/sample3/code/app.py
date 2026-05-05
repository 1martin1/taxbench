import os
import sqlite3
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from jose import jwt
from pydantic import BaseModel, EmailStr, Field
from starlette.middleware.sessions import SessionMiddleware

DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET", "development-secret-change-me")
JWT_ALGORITHM = "HS256"
TOKEN_EXPIRE_MINUTES = 60 * 24


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
                password TEXT NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def hash_password(password: str) -> str:
    import hashlib

    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        100_000,
    )
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored_password: str) -> bool:
    import hashlib
    import hmac

    try:
        salt, expected_hash = stored_password.split("$", 1)
    except ValueError:
        return False

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        100_000,
    )
    return hmac.compare_digest(digest.hex(), expected_hash)


def create_token(user_id: int, email: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=TOKEN_EXPIRE_MINUTES)).timestamp()),
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
    password: str = Field(..., min_length=1, examples=["newUserPassword"])
    name: str = Field(..., min_length=1, examples=["John Doe"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="LoginAPI",
    description="A simple webapp API for login and registration functionality.",
    version="1.0.11",
    lifespan=lifespan,
)

app.add_middleware(SessionMiddleware, secret_key=APP_SECRET)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home(request: Request) -> HTMLResponse:
    flash = request.session.pop("flash", None)
    token = request.session.get("token")
    user_email = request.session.get("user_email")

    flash_html = ""
    if flash:
        color = "#166534" if flash["type"] == "success" else "#991b1b"
        bg = "#dcfce7" if flash["type"] == "success" else "#fee2e2"
        border = "#86efac" if flash["type"] == "success" else "#fca5a5"
        flash_html = f"""
        <div style="margin-bottom:16px;padding:12px 14px;border:1px solid {border};background:{bg};color:{color};border-radius:8px;">
            {flash["message"]}
        </div>
        """

    auth_html = ""
    if token and user_email:
        auth_html = f"""
        <div style="margin-top:24px;padding:16px;border:1px solid #d1d5db;border-radius:8px;background:#f9fafb;">
            <h3 style="margin:0 0 8px 0;">Logged in</h3>
            <p style="margin:0 0 8px 0;"><strong>Email:</strong> {user_email}</p>
            <p style="margin:0;word-break:break-all;"><strong>Token:</strong> {token}</p>
        </div>
        """

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <title>LoginAPI</title>
    </head>
    <body style="font-family:Arial,sans-serif;background:#f3f4f6;margin:0;padding:40px;">
        <div style="max-width:960px;margin:0 auto;">
            <h1 style="text-align:center;">LoginAPI</h1>
            <p style="text-align:center;color:#4b5563;">Simple login and registration webapp</p>
            {flash_html}
            <div style="display:flex;gap:24px;align-items:flex-start;flex-wrap:wrap;">
                <div style="flex:1;min-width:300px;background:white;padding:24px;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,0.1);">
                    <h2 style="margin-top:0;">Login</h2>
                    <form method="post" action="/web/login">
                        <label for="login-email">Email</label><br />
                        <input id="login-email" name="email" type="email" required style="width:100%;padding:10px;margin:6px 0 14px 0;box-sizing:border-box;" />
                        <label for="login-password">Password</label><br />
                        <input id="login-password" name="password" type="password" required style="width:100%;padding:10px;margin:6px 0 14px 0;box-sizing:border-box;" />
                        <button type="submit" style="width:100%;padding:12px;background:#2563eb;color:white;border:none;border-radius:8px;cursor:pointer;">Login</button>
                    </form>
                </div>
                <div style="flex:1;min-width:300px;background:white;padding:24px;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,0.1);">
                    <h2 style="margin-top:0;">Register</h2>
                    <form method="post" action="/web/register">
                        <label for="register-name">Name</label><br />
                        <input id="register-name" name="name" type="text" required style="width:100%;padding:10px;margin:6px 0 14px 0;box-sizing:border-box;" />
                        <label for="register-email">Email</label><br />
                        <input id="register-email" name="email" type="email" required style="width:100%;padding:10px;margin:6px 0 14px 0;box-sizing:border-box;" />
                        <label for="register-password">Password</label><br />
                        <input id="register-password" name="password" type="password" required style="width:100%;padding:10px;margin:6px 0 14px 0;box-sizing:border-box;" />
                        <button type="submit" style="width:100%;padding:12px;background:#16a34a;color:white;border:none;border-radius:8px;cursor:pointer;">Register</button>
                    </form>
                </div>
            </div>
            {auth_html}
            <div style="margin-top:24px;text-align:center;">
                <a href="/docs">API Docs</a>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@app.post("/web/login", include_in_schema=False)
async def web_login(request: Request):
    form = await request.form()
    email = str(form.get("email", "")).strip()
    password = str(form.get("password", ""))

    try:
        payload = LoginRequest(email=email, password=password)
    except Exception:
        request.session["flash"] = {"type": "error", "message": "Invalid email or password"}
        return JSONResponse(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/"},
            content={"message": "Redirecting"},
        )

    conn = get_db_connection()
    try:
        row = conn.execute("SELECT id, email, password FROM users WHERE email = ?", (payload.email,)).fetchone()
    finally:
        conn.close()

    if not row or not verify_password(payload.password, row["password"]):
        request.session["flash"] = {"type": "error", "message": "Invalid email or password"}
        return JSONResponse(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/"},
            content={"message": "Redirecting"},
        )

    token = create_token(row["id"], row["email"])
    request.session["token"] = token
    request.session["user_email"] = row["email"]
    request.session["flash"] = {"type": "success", "message": "Login successful"}

    return JSONResponse(
        status_code=status.HTTP_303_SEE_OTHER,
        headers={"Location": "/"},
        content={"message": "Redirecting"},
    )


@app.post("/web/register", include_in_schema=False)
async def web_register(request: Request):
    form = await request.form()
    name = str(form.get("name", "")).strip()
    email = str(form.get("email", "")).strip()
    password = str(form.get("password", ""))

    try:
        payload = RegisterRequest(name=name, email=email, password=password)
    except Exception:
        request.session["flash"] = {"type": "error", "message": "Email already in use or invalid data"}
        return JSONResponse(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/"},
            content={"message": "Redirecting"},
        )

    conn = get_db_connection()
    try:
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (payload.email,)).fetchone()
        if existing:
            request.session["flash"] = {"type": "error", "message": "Email already in use or invalid data"}
            return JSONResponse(
                status_code=status.HTTP_303_SEE_OTHER,
                headers={"Location": "/"},
                content={"message": "Redirecting"},
            )

        conn.execute(
            "INSERT INTO users (email, password, name, created_at) VALUES (?, ?, ?, ?)",
            (
                payload.email,
                hash_password(payload.password),
                payload.name,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    request.session["flash"] = {"type": "success", "message": "Registration successful"}
    return JSONResponse(
        status_code=status.HTTP_303_SEE_OTHER,
        headers={"Location": "/"},
        content={"message": "Redirecting"},
    )


@app.post(
    "/login",
    tags=["Authentication"],
    response_model=LoginSuccessResponse,
    responses={
        200: {"description": "Successful login"},
        401: {"model": MessageResponse, "description": "Unauthorized"},
    },
)
async def login(payload: LoginRequest):
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT id, email, password FROM users WHERE email = ?", (payload.email,)).fetchone()
    finally:
        conn.close()

    if not row or not verify_password(payload.password, row["password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = create_token(row["id"], row["email"])
    return {"token": token, "message": "Login successful"}


@app.post(
    "/register",
    tags=["Authentication"],
    status_code=status.HTTP_201_CREATED,
    response_model=MessageResponse,
    responses={
        201: {"description": "Successful registration"},
        400: {"model": MessageResponse, "description": "Bad Request"},
    },
)
async def register(payload: RegisterRequest):
    conn = get_db_connection()
    try:
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (payload.email,)).fetchone()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already in use or invalid data",
            )

        conn.execute(
            "INSERT INTO users (email, password, name, created_at) VALUES (?, ?, ?, ?)",
            (
                payload.email,
                hash_password(payload.password),
                payload.name,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {"message": "Registration successful"}


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    if isinstance(exc.detail, str):
        return JSONResponse(status_code=exc.status_code, content={"message": exc.detail})
    return JSONResponse(status_code=exc.status_code, content={"message": "Request failed"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)