import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field, ValidationError
from passlib.context import CryptContext
from jose import JWTError, jwt
from starlette.responses import JSONResponse

# -------------------- Configuration --------------------
APP_SECRET = os.getenv("APP_SECRET", "defaultsecret")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 1 day

# -------------------- Security --------------------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer()


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, APP_SECRET, algorithm=ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str):
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise JWTError()
        return username
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_username(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    token = credentials.credentials
    return decode_access_token(token)


# -------------------- Database --------------------
DB_PATH = "db.sqlite3"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS secrets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            secret TEXT NOT NULL,
            FOREIGN KEY (username) REFERENCES users (username) ON DELETE CASCADE
        )
        """
    )
    conn.commit()
    conn.close()


# -------------------- Pydantic Models --------------------
class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class RegisterResponse(BaseModel):
    message: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)


class LoginResponse(BaseModel):
    token: str
    message: str


class SetSecretRequest(BaseModel):
    username: str
    secret: str


class SetSecretResponse(BaseModel):
    message: str


class GetSecretResponse(BaseModel):
    secret: str


# -------------------- FastAPI App --------------------
app = FastAPI(title="Secret Storage", version="1.0.0")


@app.on_event("startup")
def startup_event():
    init_db()


# -------------------- Endpoints --------------------
@app.post("/register", response_model=RegisterResponse, status_code=201, tags=["Authentication"])
def register(payload: RegisterRequest):
    conn = get_db()
    cursor = conn.cursor()
    # Check for existing email or username
    cursor.execute("SELECT id FROM users WHERE email = ?", (payload.email,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(
            status_code=400,
            detail="Email already in use or invalid data"
        )
    cursor.execute("SELECT id FROM users WHERE username = ?", (payload.username,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(
            status_code=400,
            detail="Username already in use or invalid data"
        )
    password_hash = get_password_hash(payload.password)
    cursor.execute(
        "INSERT INTO users (email, username, password_hash) VALUES (?, ?, ?)",
        (payload.email, payload.username, password_hash)
    )
    conn.commit()
    conn.close()
    return RegisterResponse(message="Registration successful")


@app.post("/login", response_model=LoginResponse, tags=["Authentication"])
def login(payload: LoginRequest):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT username, password_hash FROM users WHERE email = ?", (payload.email,)
    )
    row = cursor.fetchone()
    conn.close()
    if not row or not verify_password(payload.password, row["password_hash"]):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password"
        )
    access_token = create_access_token(data={"sub": row["username"]})
    return LoginResponse(token=access_token, message="Login successful")


@app.post("/set_secret", response_model=SetSecretResponse, tags=["Authentication"])
def set_secret(
    payload: SetSecretRequest,
    current_username: str = Depends(get_current_username)
):
    # Ensure the token belongs to the user attempting to set the secret
    if payload.username != current_username:
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication token"
        )
    conn = get_db()
    cursor = conn.cursor()
    # Upsert secret
    cursor.execute(
        "INSERT INTO secrets (username, secret) VALUES (?, ?) "
        "ON CONFLICT(username) DO UPDATE SET secret=excluded.secret",
        (payload.username, payload.secret)
    )
    conn.commit()
    conn.close()
    return SetSecretResponse(message="Secret has been set successfully")


@app.get("/get_secret", response_model=GetSecretResponse, tags=["Authentication"])
def get_secret(
    username: str,
    current_username: str = Depends(get_current_username)
):
    # Token must belong to the requested username
    if username != current_username:
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication token"
        )
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT secret FROM secrets WHERE username = ?", (username,)
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(
            status_code=404,
            detail="Secret not found for the given user"
        )
    return GetSecretResponse(secret=row["secret"])


# -------------------- Custom Exception Handlers --------------------
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"message": exc.detail}
    )


# -------------------- Run Server --------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)