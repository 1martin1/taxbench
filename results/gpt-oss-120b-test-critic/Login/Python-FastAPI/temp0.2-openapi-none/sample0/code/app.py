import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, Depends, Form, Request, HTTPException, status
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field, ValidationError
from passlib.context import CryptContext
from jose import JWTError, jwt
from email_validator import validate_email, EmailNotValidError

# Load environment variables (if .env is present)
from dotenv import load_dotenv

load_dotenv()

# ----------------------------------------------------------------------
# Configuration & Security
# ----------------------------------------------------------------------
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError("APP_SECRET environment variable must be set for JWT signing.")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
JWT_AUDIENCE = "loginapi"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer()

# ----------------------------------------------------------------------
# FastAPI app
# ----------------------------------------------------------------------
app = FastAPI(
    title="LoginAPI",
    description="A simple webapp API for login and registration functionality.",
    version="1.0.11",
)

# ----------------------------------------------------------------------
# Database utilities
# ----------------------------------------------------------------------
DB_PATH = "db.sqlite3"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                hashed_password TEXT NOT NULL,
                name TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


init_db()

# ----------------------------------------------------------------------
# Pydantic models
# ----------------------------------------------------------------------
class LoginRequest(BaseModel):
    email: EmailStr = Field(..., example="user@example.com")
    password: str = Field(..., example="mySecretPassword")


class RegisterRequest(BaseModel):
    email: EmailStr = Field(..., example="newuser@example.com")
    password: str = Field(..., example="newUserPassword")
    name: str = Field(..., example="John Doe")


class LoginResponse(BaseModel):
    token: str
    message: str = "Login successful"


class MessageResponse(BaseModel):
    message: str


# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(user_id: int, expires_delta: Optional[timedelta] = None) -> str:
    now = datetime.now(tz=timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "aud": JWT_AUDIENCE,
    }
    return jwt.encode(payload, APP_SECRET, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, APP_SECRET, algorithms=[ALGORITHM], audience=JWT_AUDIENCE)


def get_user_by_email(email: str) -> Optional[sqlite3.Row]:
    with get_db() as conn:
        cur = conn.execute("SELECT * FROM users WHERE email = ?", (email,))
        return cur.fetchone()


def get_user_by_id(user_id: int) -> Optional[sqlite3.Row]:
    with get_db() as conn:
        cur = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        return cur.fetchone()


def authenticate_user(email: str, password: str) -> Optional[sqlite3.Row]:
    user = get_user_by_email(email)
    if not user:
        return None
    if not verify_password(password, user["hashed_password"]):
        return None
    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> sqlite3.Row:
    token = credentials.credentials
    try:
        payload = decode_access_token(token)
        user_id = int(payload.get("sub"))
    except (JWTError, ValueError, KeyError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


# ----------------------------------------------------------------------
# API Endpoints
# ----------------------------------------------------------------------
@app.post(
    "/login",
    response_model=LoginResponse,
    responses={
        200: {"description": "Successful login"},
        401: {"model": MessageResponse, "description": "Unauthorized"},
    },
)
def login(payload: LoginRequest):
    user = authenticate_user(payload.email, payload.password)
    if not user:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"message": "Invalid email or password"},
        )
    token = create_access_token(user_id=user["id"])
    return {"token": token, "message": "Login successful"}


@app.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=MessageResponse,
    responses={
        201: {"description": "Successful registration"},
        400: {"model": MessageResponse, "description": "Bad Request"},
    },
)
def register(payload: RegisterRequest):
    # Explicit email validation (redundant but kept for clarity)
    try:
        validate_email(payload.email)
    except EmailNotValidError as exc:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": str(exc)},
        )

    if get_user_by_email(payload.email):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Email already in use or invalid data"},
        )

    hashed_password = get_password_hash(payload.password)
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO users (email, hashed_password, name) VALUES (?, ?, ?)",
                (payload.email, hashed_password, payload.name),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Email already in use or invalid data"},
        )
    return {"message": "Registration successful"}


# ----------------------------------------------------------------------
# Simple HTML pages for manual testing (embedded)
# ----------------------------------------------------------------------
LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Login</title>
</head>
<body>
    <h2>Login</h2>
    <form method="post" action="/login_form">
        <label>Email: <input type="email" name="email" required></label><br><br>
        <label>Password: <input type="password" name="password" required></label><br><br>
        <button type="submit">Login</button>
    </form>
    <p>Don't have an account? <a href="/register_form">Register here</a></p>
</body>
</html>
"""

REGISTER_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Register</title>
</head>
<body>
    <h2>Register</h2>
    <form method="post" action="/register_form">
        <label>Name: <input type="text" name="name" required></label><br><br>
        <label>Email: <input type="email" name="email" required></label><br><br>
        <label>Password: <input type="password" name="password" required></label><br><br>
        <button type="submit">Register</button>
    </form>
    <p>Already have an account? <a href="/login_form">Login here</a></p>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse(url="/login_form")


@app.get("/login_form", response_class=HTMLResponse)
def login_form():
    return HTMLResponse(content=LOGIN_HTML)


@app.get("/register_form", response_class=HTMLResponse)
def register_form():
    return HTMLResponse(content=REGISTER_HTML)


@app.post("/login_form")
def login_form_post(email: str = Form(...), password: str = Form(...)):
    try:
        payload = LoginRequest(email=email, password=password)
    except ValidationError as exc:
        return HTMLResponse(content=f"<h3>Invalid input: {exc}</h3>", status_code=400)

    response = login(payload)
    if isinstance(response, JSONResponse):
        # Error case
        return HTMLResponse(content=f"<h3>{response.body.decode()}</h3>", status_code=response.status_code)
    token = response["token"]
    return HTMLResponse(content=f"<h3>{response['message']}</h3><p>Token: {token}</p>")


@app.post("/register_form")
def register_form_post(name: str = Form(...), email: str = Form(...), password: str = Form(...)):
    try:
        payload = RegisterRequest(name=name, email=email, password=password)
    except ValidationError as exc:
        return HTMLResponse(content=f"<h3>Invalid input: {exc}</h3>", status_code=400)

    response = register(payload)
    if isinstance(response, JSONResponse):
        return HTMLResponse(content=f"<h3>{response.body.decode()}</h3>", status_code=response.status_code)
    return HTMLResponse(
        content=f"<h3>{response['message']}</h3><p>You can now <a href='/login_form'>login</a>.</p>"
    )


# ----------------------------------------------------------------------
# Run the application
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)