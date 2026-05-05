import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

import jwt
from fastapi import FastAPI, HTTPException, Depends, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field, ValidationError
from passlib.context import CryptContext
from starlette.middleware.cors import CORSMiddleware

# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------
APP_SECRET = os.getenv("APP_SECRET", "defaultsecret")  # fallback for local testing
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# ---------------------------------------------------------
# Database utilities
# ---------------------------------------------------------
DB_PATH = "db.sqlite3"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                hashed_password TEXT NOT NULL,
                name TEXT
            )
            """
        )
        conn.commit()

init_db()

# ---------------------------------------------------------
# Security utilities
# ---------------------------------------------------------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, APP_SECRET, algorithm=ALGORITHM)
    return encoded_jwt

def decode_access_token(token: str):
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    token = credentials.credentials
    payload = decode_access_token(token)
    email = payload.get("sub")
    if email is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    with get_db_connection() as conn:
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
        return {"id": user["id"], "email": user["email"], "name": user["name"]}

# ---------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------
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

# ---------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------
app = FastAPI(
    title="LoginAPI",
    description="A simple webapp API for login and registration functionality.",
    version="1.0.11",
)

# Allow all origins for simplicity (adjust in production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------
# HTML login page (simple)
# ---------------------------------------------------------
LOGIN_PAGE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Login Page</title>
    <script>
        async function login(event) {
            event.preventDefault();
            const email = document.getElementById('email').value;
            const password = document.getElementById('password').value;
            const resp = await fetch('/login', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({email, password})
            });
            const data = await resp.json();
            const output = document.getElementById('output');
            if (resp.ok) {
                output.textContent = 'Success! Token: ' + data.token;
            } else {
                output.textContent = 'Error: ' + data.message;
            }
        }
    </script>
</head>
<body>
    <h2>Login</h2>
    <form onsubmit="login(event)">
        <label>Email: <input type="email" id="email" required></label><br><br>
        <label>Password: <input type="password" id="password" required></label><br><br>
        <button type="submit">Login</button>
    </form>
    <pre id="output" style="margin-top:20px;"></pre>
    <hr>
    <h2>Register</h2>
    <form action="/register" method="post">
        <label>Email: <input type="email" name="email" required></label><br><br>
        <label>Password: <input type="password" name="password" required></label><br><br>
        <label>Name: <input type="text" name="name" required></label><br><br>
        <button type="submit">Register</button>
    </form>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse, tags=["Authentication"])
async def read_root():
    return LOGIN_PAGE_HTML

# ---------------------------------------------------------
# Register endpoint
# ---------------------------------------------------------
@app.post("/register", response_model=MessageResponse, status_code=201, tags=["Authentication"])
def register_user(payload: RegisterRequest):
    with get_db_connection() as conn:
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (payload.email,)).fetchone()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already in use or invalid data"
            )
        hashed = get_password_hash(payload.password)
        conn.execute(
            "INSERT INTO users (email, hashed_password, name) VALUES (?, ?, ?)",
            (payload.email, hashed, payload.name)
        )
        conn.commit()
    return {"message": "Registration successful"}

# ---------------------------------------------------------
# Login endpoint
# ---------------------------------------------------------
@app.post("/login", response_model=LoginResponse, tags=["Authentication"])
def login_user(payload: LoginRequest):
    with get_db_connection() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE email = ?", (payload.email,)
        ).fetchone()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password"
            )
        if not verify_password(payload.password, user["hashed_password"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password"
            )
        access_token = create_access_token(data={"sub": user["email"]})
        return {"token": access_token, "message": "Login successful"}

# ---------------------------------------------------------
# Example protected route (optional)
# ---------------------------------------------------------
@app.get("/me", response_model=MessageResponse, tags=["Authentication"])
def read_me(current_user: dict = Depends(get_current_user)):
    return {"message": f"Hello, {current_user['name']}!"}

# ---------------------------------------------------------
# Run the application
# ---------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)