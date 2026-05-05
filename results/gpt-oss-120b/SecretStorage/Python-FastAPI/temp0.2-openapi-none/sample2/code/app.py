import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

import jwt
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field, ValidationError
from passlib.context import CryptContext
from starlette.responses import JSONResponse

# -------------------- Configuration --------------------
APP_SECRET = os.getenv("APP_SECRET", "default_secret_key_change_me")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# -------------------- Database Setup --------------------
DB_PATH = "db.sqlite3"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    # Users table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL
        )
    """)
    # Secrets table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS secrets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            secret TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            UNIQUE(user_id)
        )
    """)
    conn.commit()
    conn.close()

init_db()

# -------------------- Security --------------------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, APP_SECRET, algorithm=JWT_ALGORITHM)
    return encoded_jwt

def decode_access_token(token: str):
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    token = credentials.credentials
    payload = decode_access_token(token)
    user_id = payload.get("sub")
    username = payload.get("username")
    if user_id is None or username is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    # Fetch user from DB to ensure still exists
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, username FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return {"id": row["id"], "username": row["username"]}

# -------------------- Pydantic Schemas --------------------
class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)

class SetSecretRequest(BaseModel):
    username: str
    secret: str

# -------------------- FastAPI App --------------------
app = FastAPI(title="Secret Storage", version="1.0.0")

# -------------------- Endpoints --------------------
@app.post("/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest):
    conn = get_db_connection()
    cur = conn.cursor()
    # Check email and username uniqueness
    cur.execute("SELECT id FROM users WHERE email = ?", (payload.email,))
    if cur.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Email already in use")
    cur.execute("SELECT id FROM users WHERE username = ?", (payload.username,))
    if cur.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Username already in use")
    password_hash = get_password_hash(payload.password)
    cur.execute(
        "INSERT INTO users (email, username, password_hash) VALUES (?, ?, ?)",
        (payload.email, payload.username, password_hash)
    )
    conn.commit()
    conn.close()
    return {"message": "Registration successful"}

@app.post("/login")
def login(payload: LoginRequest):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, username, password_hash FROM users WHERE email = ?", (payload.email,))
    row = cur.fetchone()
    conn.close()
    if not row or not verify_password(payload.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token_data = {"sub": row["id"], "username": row["username"]}
    token = create_access_token(token_data)
    return {"token": token, "message": "Login successful"}

@app.post("/set_secret")
def set_secret(request: SetSecretRequest, current_user: dict = Depends(get_current_user)):
    # Ensure the username in payload matches the authenticated user
    if request.username != current_user["username"]:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    conn = get_db_connection()
    cur = conn.cursor()
    # Upsert secret
    cur.execute("SELECT id FROM secrets WHERE user_id = ?", (current_user["id"],))
    existing = cur.fetchone()
    if existing:
        cur.execute(
            "UPDATE secrets SET secret = ? WHERE user_id = ?",
            (request.secret, current_user["id"])
        )
    else:
        cur.execute(
            "INSERT INTO secrets (user_id, secret) VALUES (?, ?)",
            (current_user["id"], request.secret)
        )
    conn.commit()
    conn.close()
    return {"message": "Secret has been set successfully"}

@app.get("/get_secret")
def get_secret(username: str, current_user: dict = Depends(get_current_user)):
    # Username must match authenticated user
    if username != current_user["username"]:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.secret FROM secrets s
        JOIN users u ON s.user_id = u.id
        WHERE u.username = ?
        """,
        (username,)
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Secret not found")
    return {"secret": row["secret"]}

# -------------------- Custom Exception Handlers --------------------
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"message": exc.detail})

# -------------------- Run Server --------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)