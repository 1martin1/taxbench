import os
import sqlite3
import time
import secrets
import logging
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Depends, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from passlib.context import CryptContext
from jose import JWTError, jwt
from dotenv import load_dotenv

# -------------------------------------------------------------------------
# Configuration & Environment
# -------------------------------------------------------------------------
load_dotenv()
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    # Generate a temporary secret if not provided; log a warning.
    APP_SECRET = secrets.token_urlsafe(32)
    logging.warning(
        "APP_SECRET environment variable not set. Generated a temporary secret. "
        "For production, set a stable secret via the environment."
    )

# -------------------------------------------------------------------------
# Database Setup
# -------------------------------------------------------------------------
DB_PATH = "db.sqlite3"


def get_connection():
    # check_same_thread=False allows usage across FastAPI workers/threads.
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                name TEXT NOT NULL
            )
            """
        )
        conn.commit()


init_db()


def get_user_by_email(email: str):
    with get_connection() as conn:
        cur = conn.execute("SELECT * FROM users WHERE email = ?", (email,))
        row = cur.fetchone()
        return dict(row) if row else None


def create_user(email: str, password_hash: str, name: str):
    with get_connection() as conn:
        try:
            conn.execute(
                "INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)",
                (email, password_hash, name),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already in use or invalid data",
            ) from exc


# -------------------------------------------------------------------------
# Security Utilities
# -------------------------------------------------------------------------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=30))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, APP_SECRET, algorithm="HS256")
    return encoded_jwt


def decode_access_token(token: str):
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=["HS256"])
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        ) from e


# -------------------------------------------------------------------------
# Rate Limiting (Simple in‑memory token bucket)
# -------------------------------------------------------------------------
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX_REQUESTS = 5  # per window per IP
_rate_limit_store: dict[str, list[float]] = {}


async def rate_limiter(request: Request):
    ip = request.client.host
    now = time.time()
    timestamps = _rate_limit_store.get(ip, [])
    # Keep only timestamps within the window
    timestamps = [ts for ts in timestamps if now - ts < _RATE_LIMIT_WINDOW]
    if len(timestamps) >= _RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail="Too many requests, please try again later",
        )
    timestamps.append(now)
    _rate_limit_store[ip] = timestamps


# -------------------------------------------------------------------------
# Middleware: Request Body Size Limit
# -------------------------------------------------------------------------
class BodySizeLimitMiddleware:
    def __init__(self, app: FastAPI, max_body_size: int):
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def limited_receive():
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                if len(body) > self.max_body_size:
                    response = JSONResponse(
                        {"message": "Request body too large"},
                        status_code=413,
                    )
                    await response(scope, receive, send)
                    # Stop further processing
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        await self.app(scope, limited_receive, send)


# -------------------------------------------------------------------------
# Pydantic Models
# -------------------------------------------------------------------------
class LoginRequest(BaseModel):
    email: EmailStr = Field(..., example="user@example.com")
    password: str = Field(..., min_length=1, example="mySecretPassword")


class LoginResponse(BaseModel):
    token: str = Field(..., example="jwt-token-abc123")
    message: str = Field(..., example="Login successful")


class RegisterRequest(BaseModel):
    email: EmailStr = Field(..., example="newuser@example.com")
    password: str = Field(..., min_length=1, example="newUserPassword")
    name: str = Field(..., min_length=1, example="John Doe")


class MessageResponse(BaseModel):
    message: str


# -------------------------------------------------------------------------
# FastAPI Application
# -------------------------------------------------------------------------
app = FastAPI(
    title="LoginAPI",
    description="A simple webapp API for login and registration functionality.",
    version="1.0.11",
    openapi_tags=[{"name": "Authentication", "description": "Operations related to user authentication"}],
)

# Apply body size limit middleware (1 KiB)
app.add_middleware(BodySizeLimitMiddleware, max_body_size=1024)


# Global exception handler to match OpenAPI error schema
@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"message": exc.detail})


# -------------------------------------------------------------------------
# Endpoints
# -------------------------------------------------------------------------
@app.post(
    "/register",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Authentication"],
    responses={
        201: {"description": "Successful registration"},
        400: {"description": "Bad Request"},
        429: {"description": "Too Many Requests"},
    },
)
def register_user(payload: RegisterRequest, _: None = Depends(rate_limiter)):
    if get_user_by_email(payload.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )
    password_hash = get_password_hash(payload.password)
    create_user(payload.email, password_hash, payload.name)
    return {"message": "Registration successful"}


@app.post(
    "/login",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
    tags=["Authentication"],
    responses={
        200: {"description": "Successful login"},
        401: {"description": "Unauthorized"},
        429: {"description": "Too Many Requests"},
    },
)
def login_user(payload: LoginRequest, _: None = Depends(rate_limiter)):
    user = get_user_by_email(payload.email)
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    token = create_access_token({"sub": str(user["id"]), "email": user["email"]})
    return {"token": token, "message": "Login successful"}


# -------------------------------------------------------------------------
# Run Server
# -------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)