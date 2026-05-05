import os
import sys
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, List

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, EmailStr, Field
from passlib.context import CryptContext
from jose import jwt, JWTError
from email_validator import validate_email, EmailNotValidError

# -------------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------------
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError("APP_SECRET environment variable is required for JWT signing.")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
DATABASE_PATH = "db.sqlite3"
MAX_REQUEST_SIZE = 1 * 1024 * 1024  # 1 MB
RATE_LIMIT = 10  # requests
RATE_LIMIT_WINDOW = 60  # seconds

# -------------------------------------------------------------------------
# Security utilities
# -------------------------------------------------------------------------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, APP_SECRET, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Optional[dict]:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    token = credentials.credentials
    payload = decode_access_token(token)
    return payload  # contains at least "sub" (email)


# -------------------------------------------------------------------------
# Rate limiting
# -------------------------------------------------------------------------
class RateLimiter:
    def __init__(self, limit: int, window: int):
        self.limit = limit
        self.window = window
        self.clients: Dict[str, List[float]] = {}
        self.lock = None  # type: ignore

    def is_allowed(self, client_ip: str) -> bool:
        now = time.time()
        timestamps = self.clients.get(client_ip, [])
        # Remove timestamps outside the window
        timestamps = [ts for ts in timestamps if now - ts < self.window]
        if len(timestamps) >= self.limit:
            self.clients[client_ip] = timestamps
            return False
        timestamps.append(now)
        self.clients[client_ip] = timestamps
        return True


rate_limiter = RateLimiter(RATE_LIMIT, RATE_LIMIT_WINDOW)


async def rate_limit_dependency(request: Request):
    client_ip = request.client.host
    if not rate_limiter.is_allowed(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests, please try again later.",
        )


# -------------------------------------------------------------------------
# Request size limit middleware
# -------------------------------------------------------------------------
class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        body = await request.body()
        if len(body) > MAX_REQUEST_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Request payload too large.",
            )
        # Re-create request with the original body for downstream handlers
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request._receive = receive  # type: ignore
        response = await call_next(request)
        return response


# -------------------------------------------------------------------------
# Database utilities
# -------------------------------------------------------------------------
def get_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    try:
        with get_db() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    hashed_password TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()
    except sqlite3.Error as e:
        print(f"Database initialization failed: {e}", file=sys.stderr)
        sys.exit(1)


def get_user_by_email(email: str) -> Optional[sqlite3.Row]:
    with get_db() as conn:
        cur = conn.execute("SELECT * FROM users WHERE email = ?", (email,))
        return cur.fetchone()


def create_user(email: str, password: str, name: str) -> sqlite3.Row:
    hashed = get_password_hash(password)
    now = datetime.utcnow().isoformat()
    try:
        with get_db() as conn:
            cur = conn.execute(
                "INSERT INTO users (email, hashed_password, name, created_at) VALUES (?, ?, ?, ?)",
                (email, hashed, name, now),
            )
            conn.commit()
            user_id = cur.lastrowid
            cur = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            return cur.fetchone()
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )
    except sqlite3.Error as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )


# -------------------------------------------------------------------------
# Pydantic models
# -------------------------------------------------------------------------
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


class MessageResponse(BaseModel):
    message: str


# -------------------------------------------------------------------------
# FastAPI app
# -------------------------------------------------------------------------
app = FastAPI(
    title="LoginAPI",
    description="A simple webapp API for login and registration functionality.",
    version="1.0.11",
    openapi_tags=[{"name": "Authentication", "description": "Operations related to user authentication"}],
)

app.add_middleware(BodySizeLimitMiddleware)


@app.on_event("startup")
def on_startup():
    init_db()


@app.post(
    "/login",
    response_model=LoginResponse,
    responses={401: {"model": MessageResponse}, 429: {"model": MessageResponse}},
    tags=["Authentication"],
    summary="User login",
    description="Authenticate a user with email and password.",
    dependencies=[Depends(rate_limit_dependency)],
)
def login(payload: LoginRequest):
    user = get_user_by_email(payload.email)
    if not user or not verify_password(payload.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    access_token = create_access_token(data={"sub": payload.email})
    return {"token": access_token, "message": "Login successful"}


@app.post(
    "/register",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    responses={400: {"model": MessageResponse}, 429: {"model": MessageResponse}},
    tags=["Authentication"],
    summary="User registration",
    description="Register a new user with email, password, and name.",
    dependencies=[Depends(rate_limit_dependency)],
)
def register(payload: RegisterRequest):
    # Email format already validated by Pydantic; additional validation for safety
    try:
        validate_email(payload.email)
    except EmailNotValidError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email address",
        )
    # Attempt to create user; create_user handles duplicate email
    create_user(email=payload.email, password=payload.password, name=payload.name)
    return {"message": "Registration successful"}


# Example protected endpoint to demonstrate bearer authentication usage
@app.get(
    "/me",
    response_model=MessageResponse,
    tags=["Authentication"],
    summary="Get current user info",
    description="Returns a message containing the authenticated user's email.",
)
def read_current_user(current_user: dict = Depends(get_current_user)):
    email = current_user.get("sub")
    return {"message": f"Authenticated as {email}"}


# -------------------------------------------------------------------------
# Run server
# -------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)