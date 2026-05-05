import os
import re
import hmac
import sqlite3
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager

import uvicorn
import jwt
from fastapi import FastAPI, HTTPException, Depends, Header, Query, status
from fastapi.responses import JSONResponse
from fastapi.security.utils import get_authorization_scheme_param
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from passlib.context import CryptContext

DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET", "change-this-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXP_MINUTES = 60 * 24

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = get_db_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS secrets (
                user_id INTEGER PRIMARY KEY,
                secret TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_username(username: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_]{3,50}", username))


def create_access_token(user_id: int, email: str, username: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "username": username,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=JWT_EXP_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, APP_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )


def unauthorized_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication token",
    )


def get_bearer_token(authorization: str | None = Header(default=None)) -> str:
    if not authorization:
        raise unauthorized_exception()
    scheme, token = get_authorization_scheme_param(authorization)
    if scheme.lower() != "bearer" or not token:
        raise unauthorized_exception()
    return token


def get_current_user(token: str = Depends(get_bearer_token)) -> dict:
    payload = decode_access_token(token)
    user_id = payload.get("sub")
    email = payload.get("email")
    username = payload.get("username")
    if not user_id or not email or not username:
        raise unauthorized_exception()

    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT id, email, username FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        raise unauthorized_exception()

    if not (
        hmac.compare_digest(str(row["id"]), str(user_id))
        and hmac.compare_digest(row["email"], str(email))
        and hmac.compare_digest(row["username"], str(username))
    ):
        raise unauthorized_exception()

    return {"id": row["id"], "email": row["email"], "username": row["username"]}


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=256)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class SetSecretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=50)
    secret: str = Field(min_length=1, max_length=100000)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Secret Storage",
    description="A secure cloud storage app for string secrets.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException):
    if isinstance(exc.detail, str):
        return JSONResponse(status_code=exc.status_code, content={"message": exc.detail})
    return JSONResponse(status_code=exc.status_code, content={"message": "Request failed"})


@app.post("/register", status_code=201)
def register(payload: RegisterRequest):
    if not validate_username(payload.username):
        raise HTTPException(status_code=400, detail="Email already in use or invalid data")

    password_hash = pwd_context.hash(payload.password)
    conn = get_db_connection()
    try:
        existing = conn.execute(
            "SELECT 1 FROM users WHERE email = ? OR username = ?",
            (payload.email, payload.username),
        ).fetchone()
        if existing is not None:
            raise HTTPException(status_code=400, detail="Email already in use or invalid data")

        conn.execute(
            """
            INSERT INTO users (email, username, password_hash, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (payload.email, payload.username, password_hash, utc_now_iso()),
        )
        conn.commit()
    finally:
        conn.close()

    return {"message": "Registration successful"}


@app.post("/login")
def login(payload: LoginRequest):
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT id, email, username, password_hash FROM users WHERE email = ?",
            (payload.email,),
        ).fetchone()
    finally:
        conn.close()

    if row is None or not pwd_context.verify(payload.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(row["id"], row["email"], row["username"])
    return {"token": token, "message": "Login successful"}


@app.post("/set_secret")
def set_secret(payload: SetSecretRequest, current_user: dict = Depends(get_current_user)):
    if not validate_username(payload.username):
        raise HTTPException(status_code=400, detail="Invalid data")

    if not hmac.compare_digest(current_user["username"], payload.username):
        raise HTTPException(status_code=401, detail="Invalid authentication token")

    conn = get_db_connection()
    try:
        user_row = conn.execute(
            "SELECT id FROM users WHERE username = ?",
            (payload.username,),
        ).fetchone()
        if user_row is None:
            raise HTTPException(status_code=401, detail="Invalid authentication token")

        conn.execute(
            """
            INSERT INTO secrets (user_id, secret, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                secret = excluded.secret,
                updated_at = excluded.updated_at
            """,
            (user_row["id"], payload.secret, utc_now_iso()),
        )
        conn.commit()
    finally:
        conn.close()

    return {"message": "Secret has been set successfully"}


@app.get("/get_secret")
def get_secret(
    username: str = Query(..., min_length=3, max_length=50),
    current_user: dict = Depends(get_current_user),
):
    if not validate_username(username):
        raise HTTPException(status_code=400, detail="Invalid data")

    if not hmac.compare_digest(current_user["username"], username):
        raise HTTPException(status_code=401, detail="Invalid authentication token")

    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT s.secret
            FROM secrets s
            INNER JOIN users u ON u.id = s.user_id
            WHERE u.username = ?
            """,
            (username,),
        ).fetchone()
    finally:
        conn.close()

    return {"secret": row["secret"] if row is not None else ""}


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)