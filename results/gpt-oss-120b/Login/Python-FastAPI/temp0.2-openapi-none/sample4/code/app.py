import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

import jwt
from fastapi import FastAPI, HTTPException, Depends, Request, Form, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, ValidationError
from email_validator import validate_email, EmailNotValidError
from jinja2 import Template

# Initialize FastAPI app
app = FastAPI(title="LoginAPI", version="1.0.11")

# Security
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer()

# JWT secret
JWT_SECRET = os.getenv("APP_SECRET", "defaultsecret")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = 30

# Database file
DB_PATH = "db.sqlite3"

# ---------- Database utilities ----------
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
                name TEXT,
                hashed_password TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()

@app.on_event("startup")
def on_startup():
    init_db()

# ---------- Pydantic models ----------
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    name: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

# ---------- Helper functions ----------
def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=JWT_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return encoded_jwt

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        return email
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate token")

# ---------- Routes ----------
@app.get("/", response_class=HTMLResponse)
def read_root():
    html_template = Template("""
    <!doctype html>
    <html>
    <head><title>Login Page</title></head>
    <body>
        <h2>Login</h2>
        <form action="/login" method="post">
            Email: <input type="email" name="email" required/><br/>
            Password: <input type="password" name="password" required/><br/>
            <button type="submit">Login</button>
        </form>
        <h2>Register</h2>
        <form action="/register" method="post">
            Name: <input type="text" name="name" required/><br/>
            Email: <input type="email" name="email" required/><br/>
            Password: <input type="password" name="password" required/><br/>
            <button type="submit">Register</button>
        </form>
    </body>
    </html>
    """)
    return html_template.render()

@app.post("/register")
def register(
    email: str = Form(...),
    password: str = Form(...),
    name: str = Form(...)
):
    # Validate email format
    try:
        valid = validate_email(email)
        email = valid.email
    except EmailNotValidError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Basic validation
    if not password or not name:
        raise HTTPException(status_code=400, detail="Password and name are required")

    hashed_pw = hash_password(password)
    created_at = datetime.utcnow().isoformat()

    with get_db_connection() as conn:
        try:
            conn.execute(
                "INSERT INTO users (email, name, hashed_password, created_at) VALUES (?, ?, ?, ?)",
                (email, name, hashed_pw, created_at)
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="Email already in use")

    return JSONResponse(
        status_code=201,
        content={"message": "Registration successful"}
    )

@app.post("/login")
def login(
    email: str = Form(...),
    password: str = Form(...)
):
    # Validate email format
    try:
        valid = validate_email(email)
        email = valid.email
    except EmailNotValidError as e:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    with get_db_connection() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()

    if not user or not verify_password(password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(data={"sub": email})
    return {"token": token, "message": "Login successful"}

# Example protected endpoint (not required by spec but demonstrates bearer auth)
@app.get("/protected")
def protected_route(current_user: str = Depends(get_current_user)):
    return {"message": f"Hello, {current_user}. This is a protected route."}

# ---------- Run server ----------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)