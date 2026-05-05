import os
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Callable, Dict, List

from fastapi import (
    FastAPI,
    HTTPException,
    Depends,
    status,
    Request,
)
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, EmailStr, Field, validator
from passlib.context import CryptContext
from jose import JWTError, jwt
from email_validator import validate_email, EmailNotValidError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError("APP_SECRET environment variable must be set for production.")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
DATABASE_PATH = "db.sqlite3"

# ---------------------------------------------------------------------------
# Security utilities
# ---------------------------------------------------------------------------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer()


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, APP_SECRET, algorithm=ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=[ALGORITHM])
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from e


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    token = credentials.credentials
    payload = decode_access_token(token)
    email: str = payload.get("sub")
    if email is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload"
        )
    user = get_user_by_email(email)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )
    return user


# ---------------------------------------------------------------------------
# Database utilities
# ---------------------------------------------------------------------------
def get_db_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                hashed_password TEXT NOT NULL
            );
            """
        )
        conn.commit()


def get_user_by_email(email: str):
    with get_db_connection() as conn:
        cur = conn.execute("SELECT * FROM users WHERE email = ?", (email,))
        row = cur.fetchone()
        return dict(row) if row else None


def create_user(email: str, name: str, hashed_password: str):
    with get_db_connection() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, name, hashed_password) VALUES (?, ?, ?)",
            (email, name, hashed_password),
        )
        conn.commit()
        return cur.lastrowid


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX_REQUESTS = 5
_rate_limit_store: Dict[str, List[float]] = {}


def rate_limiter(request: Request):
    client_ip = request.client.host
    now = time.time()
    timestamps = _rate_limit_store.get(client_ip, [])
    # Remove timestamps older than the window
    timestamps = [ts for ts in timestamps if now - ts < _RATE_LIMIT_WINDOW]
    if len(timestamps) >= _RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests, please try again later.",
        )
    timestamps.append(now)
    _rate_limit_store[client_ip] = timestamps


# ---------------------------------------------------------------------------
# Request size limiting middleware
# ---------------------------------------------------------------------------
MAX_JSON_BODY_SIZE = 1024  # 1 KiB


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_JSON_BODY_SIZE:
                    return JSONResponse(
                        {"message": "Request body too large"},
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    )
            except ValueError:
                pass  # ignore malformed header, let FastAPI handle it later
        else:
            # If no Content-Length, read the body to enforce limit
            body = await request.body()
            if len(body) > MAX_JSON_BODY_SIZE:
                return JSONResponse(
                    {"message": "Request body too large"},
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )
            # Re-inject the body for downstream handlers
            request = Request(request.scope, receive=lambda: {"type": "http.request", "body": body})
        response = await call_next(request)
        return response


# ---------------------------------------------------------------------------
# Pydantic models (request / response schemas)
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    email: EmailStr = Field(..., example="user@example.com")
    password: str = Field(..., example="mySecretPassword")


class LoginResponse(BaseModel):
    token: str = Field(..., example="jwt-token-abc123")
    message: str = Field(..., example="Login successful")


class RegisterRequest(BaseModel):
    email: EmailStr = Field(..., example="newuser@example.com")
    password: str = Field(..., example="newUserPassword")
    name: str = Field(..., example="John Doe")

    @validator("password")
    def validate_password_length(cls, v: str) -> str:
        if not (8 <= len(v) <= 128):
            raise ValueError("Password must be between 8 and 128 characters")
        return v


class RegisterResponse(BaseModel):
    message: str = Field(..., example="Registration successful")


class ErrorResponse(BaseModel):
    message: str = Field(..., example="Invalid email or password")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="LoginAPI",
    description="A simple webapp API for login and registration functionality.",
    version="1.0.11",
    openapi_tags=[{"name": "Authentication", "description": "Operations related to user authentication"}],
)


# Add middleware
app.add_middleware(BodySizeLimitMiddleware)


# Custom exception handler to match OpenAPI error schema
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # For defined error responses, map 'detail' to 'message'
    if exc.status_code in {400, 401, 429, 413}:
        return JSONResponse(
            status_code=exc.status_code,
            content={"message": exc.detail},
        )
    # Fallback to default behavior for other status codes
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.on_event("startup")
def on_startup():
    init_db()


@app.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Authentication"],
    responses={400: {"model": ErrorResponse}},
    dependencies=[Depends(rate_limiter)],
)
def register(payload: RegisterRequest):
    # Additional email validation for clearer messages
    try:
        validate_email(payload.email)
    except EmailNotValidError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if get_user_by_email(payload.email):
        raise HTTPException(status_code=400, detail="Email already in use")

    hashed_pw = get_password_hash(payload.password)
    create_user(email=payload.email, name=payload.name, hashed_password=hashed_pw)

    return RegisterResponse(message="Registration successful")


@app.post(
    "/login",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
    tags=["Authentication"],
    responses={401: {"model": ErrorResponse}},
    dependencies=[Depends(rate_limiter)],
)
def login(payload: LoginRequest):
    user = get_user_by_email(payload.email)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not verify_password(payload.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    access_token = create_access_token(data={"sub": user["email"]})
    return LoginResponse(token=access_token, message="Login successful")


# ---------------------------------------------------------------------------
# Run server
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)