import os
import sqlite3
from datetime import datetime, timedelta
from typing import Generator

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, constr
from passlib.context import CryptContext
from jose import JWTError, jwt
import uvicorn

# ------------------- Configuration -------------------
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError("APP_SECRET environment variable must be set for JWT signing")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
DATABASE = "db.sqlite3"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)

# ------------------- Database Utilities -------------------
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(
        DATABASE,
        timeout=30,                # prevent immediate "database is locked" errors
        check_same_thread=False,   # allow usage in multiple threads
    )
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db() -> None:
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                name TEXT NOT NULL
            );
            """
        )
        # Enable WAL mode for better concurrency
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.commit()

# ------------------- Pydantic Schemas -------------------
class LoginRequest(BaseModel):
    email: EmailStr = Field(..., max_length=254, example="user@example.com")
    password: constr(min_length=8, max_length=128) = Field(..., example="mySecretPassword")

class RegisterRequest(BaseModel):
    email: EmailStr = Field(..., max_length=254, example="newuser@example.com")
    password: constr(min_length=8, max_length=128) = Field(..., example="newUserPassword")
    name: constr(min_length=1, max_length=100) = Field(..., example="John Doe")

class TokenResponse(BaseModel):
    token: str = Field(..., example="jwt-token-abc123")
    message: str = Field(..., example="Login successful")

class MessageResponse(BaseModel):
    message: str

# ------------------- Helper Functions -------------------
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, APP_SECRET, algorithm=ALGORITHM)
    return encoded_jwt

def get_user_by_email(conn: sqlite3.Connection, email: str):
    cur = conn.execute("SELECT * FROM users WHERE email = ?", (email,))
    return cur.fetchone()

def create_user(conn: sqlite3.Connection, email: str, password: str, name: str):
    password_hash = get_password_hash(password)
    cur = conn.execute(
        "INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)",
        (email, password_hash, name),
    )
    conn.commit()
    return cur.lastrowid

# ------------------- FastAPI Application -------------------
app = FastAPI(
    title="LoginAPI",
    description="A simple webapp API for login and registration functionality.",
    version="1.0.11",
    openapi_tags=[{"name": "Authentication", "description": "Operations related to user authentication"}],
)

@app.on_event("startup")
def on_startup():
    init_db()

@app.post(
    "/login",
    response_model=TokenResponse,
    responses={401: {"model": MessageResponse}},
    tags=["Authentication"],
    summary="User login",
    description="Authenticate a user with email and password."
)
def login(request: LoginRequest, db: sqlite3.Connection = Depends(get_db)):
    user = get_user_by_email(db, request.email)
    if not user or not verify_password(request.password, user["password_hash"]):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"message": "Invalid email or password"},
        )
    token_data = {"sub": user["id"], "email": user["email"]}
    token = create_access_token(token_data)
    return TokenResponse(token=token, message="Login successful")

@app.post(
    "/register",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    responses={400: {"model": MessageResponse}},
    tags=["Authentication"],
    summary="User registration",
    description="Register a new user with email, password, and name."
)
def register(request: RegisterRequest, db: sqlite3.Connection = Depends(get_db)):
    if get_user_by_email(db, request.email):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Email already in use or invalid data"},
        )
    try:
        create_user(db, request.email, request.password, request.name)
    except sqlite3.IntegrityError:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Email already in use or invalid data"},
        )
    return MessageResponse(message="Registration successful")

# Optional: endpoint to test token (not required by spec)
@app.get(
    "/me",
    response_model=MessageResponse,
    tags=["Authentication"],
    summary="Get current user info",
    description="Returns a simple message confirming token validity."
)
def read_me(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: sqlite3.Connection = Depends(get_db)
):
    if not credentials:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"message": "Not authenticated"},
        )
    token = credentials.credentials
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise JWTError()
    except JWTError:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"message": "Invalid token"},
        )
    cur = db.execute("SELECT email FROM users WHERE id = ?", (user_id,))
    user = cur.fetchone()
    if not user:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"message": "User not found"},
        )
    return MessageResponse(message=f"Authenticated as {user['email']}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)