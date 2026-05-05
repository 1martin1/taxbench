import os
import sqlite3
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    status,
    FastAPIHTTPException,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, StringConstraints
from passlib.hash import bcrypt
import jwt
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# Rate limiting configuration
REQUEST_LIMIT = 5  # Max requests per period
RATE_LIMIT_PERIOD = 60  # Seconds
request_counts = {}  # In-memory storage for rate limiting


def check_rate_limit(ip: str, endpoint: str):
    """Check rate limit for a given IP and endpoint."""
    key = (endpoint, ip)
    now = datetime.now()
    if key not in request_counts:
        request_counts[key] = []
    timestamps = request_counts[key]
    # Remove timestamps older than the rate limit period
    timestamps[:] = [t for t in timestamps if (now - t).total_seconds() < RATE_LIMIT_PERIOD]
    if len(timestamps) >= REQUEST_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="Too many requests, please try again later"
        )
    timestamps.append(now)


def get_db():
    """Get a database connection with error handling."""
    try:
        db = sqlite3.connect('db.sqlite3')
        db.row_factory = sqlite3.Row
        return db
    except sqlite3.Error as e:
        raise HTTPException(
            status_code=500,
            detail="Database connection error"
        ) from e


@app.on_event("startup")
def startup_event():
    """Startup event to validate APP_SECRET and create tables."""
    if not os.getenv("APP_SECRET"):
        raise RuntimeError("APP_SECRET environment variable is missing")
    create_tables()


def create_tables():
    """Create database tables if they don't exist."""
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            password_hash TEXT NOT NULL
        )
    """)
    db.commit()
    db.close()


# Pydantic models with validation constraints
NameStr = Annotated[str, StringConstraints(max_length=100)]
PasswordStr = Annotated[str, StringConstraints(max_length=1000)]


class RegisterRequest(BaseModel):
    email: EmailStr
    password: PasswordStr
    name: NameStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: PasswordStr


class LoginResponse(BaseModel):
    token: str
    message: str


class RegisterResponse(BaseModel):
    message: str


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError
):
    """Convert Pydantic validation errors to 400 Bad Request."""
    return JSONResponse(
        status_code=400,
        content={"message": "Invalid email or password"}
    )


@app.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
def register(user_data: RegisterRequest, request: Request):
    """Register a new user with email, password, and name."""
    ip = request.client.host
    check_rate_limit(ip, "/register")
    db = get_db()
    try:
        password_hash = bcrypt.hash(user_data.password)
        db.execute(
            "INSERT INTO users (email, name, password_hash) VALUES (?, ?, ?)",
            (user_data.email, user_data.name, password_hash)
        )
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=400,
            detail="Email already in use or invalid data"
        )
    finally:
        db.close()
    return {"message": "Registration successful"}


@app.post("/login", response_model=LoginResponse)
def login(credentials: LoginRequest, request: Request):
    """Authenticate a user with email and password."""
    ip = request.client.host
    check_rate_limit(ip, "/login")
    db = get_db()
    user = db.execute(
        "SELECT * FROM users WHERE email = ?",
        (credentials.email,)
    ).fetchone()
    db.close()
    if not user or not bcrypt.verify(credentials.password, user['password_hash']):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password"
        )
    secret = os.getenv("APP_SECRET")
    if not secret:
        raise HTTPException(
            status_code=500,
            detail="Internal server error"
        )
    token = jwt.encode(
        {
            "sub": user['email'],
            "exp": datetime.utcnow() + timedelta(hours=1)
        },
        secret,
        algorithm="HS256"
    )
    return {"token": token, "message": "Login successful"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)