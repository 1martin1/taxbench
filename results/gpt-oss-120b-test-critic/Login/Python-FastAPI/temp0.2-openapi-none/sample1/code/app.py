import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

import jwt
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr, Field, ValidationError, validator
from passlib.context import CryptContext
from starlette.middleware.cors import CORSMiddleware
from jinja2 import Template

# -------------------------------------------------
# Configuration
# -------------------------------------------------
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError("APP_SECRET environment variable not set")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# -------------------------------------------------
# Password hashing
# -------------------------------------------------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


# -------------------------------------------------
# Database utilities
# -------------------------------------------------
DB_PATH = "db.sqlite3"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                name TEXT,
                hashed_password TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


# -------------------------------------------------
# Pydantic models
# -------------------------------------------------
class LoginRequest(BaseModel):
    email: EmailStr = Field(..., example="user@example.com")
    password: str = Field(..., min_length=1, example="mySecretPassword")


class RegisterRequest(BaseModel):
    email: EmailStr = Field(..., example="newuser@example.com")
    password: str = Field(..., min_length=1, example="newUserPassword")
    name: str = Field(..., example="John Doe")

    @validator("name")
    def name_not_empty(cls, v):
        if not v.strip():
            raise ValueError("Name cannot be empty")
        return v


class TokenResponse(BaseModel):
    token: str
    message: str = "Login successful"


class MessageResponse(BaseModel):
    message: str


# -------------------------------------------------
# JWT utilities
# -------------------------------------------------
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, APP_SECRET, algorithm=ALGORITHM)
    return encoded_jwt


# -------------------------------------------------
# FastAPI app
# -------------------------------------------------
app = FastAPI(
    title="LoginAPI",
    description="A simple webapp API for login and registration functionality.",
    version="1.0.11",
)

# Allow CORS for testing in browsers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()


# -------------------------------------------------
# HTML Templates (inline)
# -------------------------------------------------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Simple Login App</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 2rem; }}
        .container {{ max-width: 400px; margin: auto; }}
        form {{ border: 1px solid #ccc; padding: 1rem; margin-bottom: 1rem; }}
        input[type=text], input[type=password], input[type=email] {{
            width: 100%; padding: 0.5rem; margin: 0.5rem 0; box-sizing: border-box;
        }}
        button {{ padding: 0.5rem 1rem; }}
        .msg {{ color: red; }}
    </style>
</head>
<body>
<div class="container">
    <h2>Register</h2>
    <form method="post" action="/register">
        <label>Name:</label><br/>
        <input type="text" name="name" required/><br/>
        <label>Email:</label><br/>
        <input type="email" name="email" required/><br/>
        <label>Password:</label><br/>
        <input type="password" name="password" required/><br/>
        <button type="submit">Register</button>
    </form>

    <h2>Login</h2>
    <form method="post" action="/login">
        <label>Email:</label><br/>
        <input type="email" name="email" required/><br/>
        <label>Password:</label><br/>
        <input type="password" name="password" required/><br/>
        <button type="submit">Login</button>
    </form>

    {% if message %}
    <p class="msg">{{ message }}</p>
    {% endif %}
</div>
</body>
</html>
"""


def render_html(message: Optional[str] = None) -> str:
    tmpl = Template(HTML_TEMPLATE)
    return tmpl.render(message=message)


# -------------------------------------------------
# Routes
# -------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def read_root():
    return render_html()


@app.post(
    "/register",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_user(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Register a new user.
    Accepts both JSON and form data.
    """
    # Try JSON first
    try:
        payload = await request.json()
    except Exception:
        payload = None

    if not payload:
        form = await request.form()
        payload = {
            "email": form.get("email"),
            "password": form.get("password"),
            "name": form.get("name"),
        }

    try:
        data = RegisterRequest(**payload)
    except ValidationError:
        raise HTTPException(status_code=400, detail="Invalid registration data")

    cursor = db.cursor()
    cursor.execute("SELECT id FROM users WHERE email = ?", (data.email,))
    if cursor.fetchone():
        raise HTTPException(
            status_code=400,
            detail="Email already in use or invalid data",
        )

    hashed_pw = get_password_hash(data.password)
    cursor.execute(
        "INSERT INTO users (email, name, hashed_password) VALUES (?, ?, ?)",
        (data.email, data.name, hashed_pw),
    )
    db.commit()
    return {"message": "Registration successful"}


@app.post("/login", response_model=TokenResponse)
async def login_user(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Authenticate a user and return a JWT token.
    Accepts both JSON and form data.
    """
    # Try JSON first
    try:
        payload = await request.json()
    except Exception:
        payload = None

    if not payload:
        form = await request.form()
        payload = {
            "email": form.get("email"),
            "password": form.get("password"),
        }

    try:
        data = LoginRequest(**payload)
    except ValidationError:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    cursor = db.cursor()
    cursor.execute(
        "SELECT id, hashed_password FROM users WHERE email = ?",
        (data.email,),
    )
    user = cursor.fetchone()
    if not user or not verify_password(data.password, user["hashed_password"]):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password",
        )

    access_token = create_access_token(data={"sub": data.email})
    return {"token": access_token, "message": "Login successful"}


# -------------------------------------------------
# Run server
# -------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)