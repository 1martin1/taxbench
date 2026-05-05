import os
import sqlite3
import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, EmailStr, Field, ValidationError
from passlib.context import CryptContext
from jose import jwt, JWTError
from starlette.middleware.cors import CORSMiddleware
from starlette.templating import Jinja2Templates
from starlette.staticfiles import StaticFiles
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
APP_SECRET = os.getenv("APP_SECRET", "CHANGE_ME_TO_A_STRONG_SECRET")

# FastAPI app
app = FastAPI(
    title="LoginAPI",
    description="A simple webapp API for login and registration functionality.",
    version="1.0.11",
)

# CORS (optional, allows testing from browsers)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Templates for HTML page
templates = Jinja2Templates(directory="templates")  # dummy, we will embed template string

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT settings
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# Database utilities
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
                password_hash TEXT NOT NULL
            )
            """
        )
        conn.commit()


@app.on_event("startup")
def on_startup():
    init_db()


# Pydantic models
class RegisterRequest(BaseModel):
    email: EmailStr = Field(..., example="newuser@example.com")
    password: str = Field(..., min_length=4, example="newUserPassword")
    name: str = Field(..., example="John Doe")


class LoginRequest(BaseModel):
    email: EmailStr = Field(..., example="user@example.com")
    password: str = Field(..., example="mySecretPassword")


class TokenResponse(BaseModel):
    token: str
    message: str = "Login successful"


class MessageResponse(BaseModel):
    message: str


# Helper functions
def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[datetime.timedelta] = None):
    to_encode = data.copy()
    expire = datetime.datetime.utcnow() + (expires_delta or datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, APP_SECRET, algorithm=ALGORITHM)
    return encoded_jwt


def get_user_by_email(conn: sqlite3.Connection, email: str):
    cur = conn.execute("SELECT * FROM users WHERE email = ?", (email,))
    return cur.fetchone()


def get_user_by_id(conn: sqlite3.Connection, user_id: int):
    cur = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    return cur.fetchone()


# Routes
@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    # Simple HTML page with login form
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Login Page</title>
        <style>
            body {font-family: Arial, sans-serif; margin: 40px;}
            .container {max-width: 400px; margin: auto;}
            input {width: 100%; padding: 8px; margin: 5px 0;}
            button {padding: 10px; width: 100%;}
            .msg {margin-top: 20px; color: green;}
            .error {margin-top: 20px; color: red;}
        </style>
    </head>
    <body>
        <div class="container">
            <h2>Login</h2>
            <form id="loginForm">
                <input type="email" id="email" placeholder="Email" required />
                <input type="password" id="password" placeholder="Password" required />
                <button type="submit">Login</button>
            </form>
            <div id="response" class="msg"></div>
            <div id="error" class="error"></div>
        </div>
        <script>
            const form = document.getElementById('loginForm');
            form.addEventListener('submit', async (e) => {
                e.preventDefault();
                const email = document.getElementById('email').value;
                const password = document.getElementById('password').value;
                const respDiv = document.getElementById('response');
                const errDiv = document.getElementById('error');
                respDiv.textContent = '';
                errDiv.textContent = '';
                try {
                    const res = await fetch('/login', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({email, password})
                    });
                    const data = await res.json();
                    if (res.ok) {
                        respDiv.textContent = data.message + ' Token: ' + data.token;
                    } else {
                        errDiv.textContent = data.message || 'Login failed';
                    }
                } catch (err) {
                    errDiv.textContent = 'Error: ' + err;
                }
            });
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@app.post("/register", response_model=MessageResponse, status_code=201, responses={
    400: {"model": MessageResponse}
})
def register_user(payload: RegisterRequest, db: sqlite3.Connection = Depends(get_db)):
    # Check if email already exists
    if get_user_by_email(db, payload.email):
        raise HTTPException(status_code=400, detail="Email already in use or invalid data")
    # Hash password and insert user
    password_hash = get_password_hash(payload.password)
    try:
        db.execute(
            "INSERT INTO users (email, name, password_hash) VALUES (?, ?, ?)",
            (payload.email, payload.name, password_hash),
        )
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Email already in use or invalid data")
    return {"message": "Registration successful"}


@app.post("/login", response_model=TokenResponse, responses={
    401: {"model": MessageResponse}
})
def login_user(payload: LoginRequest, db: sqlite3.Connection = Depends(get_db)):
    user = get_user_by_email(db, payload.email)
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token_data = {"sub": str(user["id"]), "email": user["email"]}
    token = create_access_token(token_data)
    return {"token": token, "message": "Login successful"}


# Dependency to get current user from token (optional, not used in current endpoints)
async def get_current_user(token: str = Depends(oauth2_scheme), db: sqlite3.Connection = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=401,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = get_user_by_id(db, int(user_id))
    if user is None:
        raise credentials_exception
    return user


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)