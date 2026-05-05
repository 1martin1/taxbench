import os
import sqlite3
import datetime
from fastapi import FastAPI, Depends, HTTPException, status, Query, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from typing import Annotated
from jose import JWTError, jwt
from passlib.context import CryptContext
from starlette.middleware.base import BaseHTTPMiddleware
import time

app = FastAPI()

DATABASE_FILE = "db.sqlite3"

# Rate limiting middleware
class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests=5, window=60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window
        self.requests = {}

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in ["/register", "/login"]:
            client_host = request.client.host
            now = time.time()

            if client_host not in self.requests:
                self.requests[client_host] = []
            else:
                self.requests[client_host] = [t for t in self.requests[client_host] if now - t <= self.window]

            if len(self.requests[client_host]) >= self.max_requests:
                raise HTTPException(status_code=429, detail="Too many requests")

            self.requests[client_host].append(now)
        response = await call_next(request)
        return response

app.add_middleware(RateLimitMiddleware, max_requests=5, window=60)

# Initialize database
def init_db():
    with sqlite3.connect(DATABASE_FILE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                email TEXT UNIQUE,
                username TEXT UNIQUE,
                password_hash TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS secrets (
                username TEXT PRIMARY KEY,
                secret TEXT
            )
        """)
        conn.commit()

init_db()

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# OAuth2 setup
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# APP_SECRET from environment
APP_SECRET = os.environ.get("APP_SECRET")
if not APP_SECRET:
    raise ValueError("APP_SECRET environment variable not set")

ALGORITHM = "HS256"

# Helper functions
def get_db():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def create_access_token(username: str):
    to_encode = {"sub": username}
    expire = datetime.datetime.utcnow() + datetime.timedelta(minutes=30)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, APP_SECRET, algorithm=ALGORITHM)
    return encoded_jwt

# Pydantic models
class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(max_length=50)
    password: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class SetSecretRequest(BaseModel):
    username: str = Field(max_length=50)
    secret: str = Field(max_length=1000)

# Dependency to get current user from token
def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    return username

# Endpoints
@app.post("/register", status_code=201)
async def register(request: RegisterRequest):
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO users (email, username, password_hash) VALUES (?, ?, ?)",
                (request.email, request.username, pwd_context.hash(request.password))
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="Email already in use or invalid data")
    return {"message": "Registration successful"}

@app.post("/login")
async def login(request: LoginRequest):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT password_hash FROM users WHERE email = ?", (request.email,))
        user = cursor.fetchone()
        if not user or not pwd_context.verify(request.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        # Get the username
        cursor.execute("SELECT username FROM users WHERE email = ?", (request.email,))
        username = cursor.fetchone()["username"]
        token = create_access_token(username)
        return {"token": token, "message": "Login successful"}

@app.post("/set_secret")
async def set_secret(request: SetSecretRequest, current_user: Annotated[str, Depends(get_current_user)]):
    if request.username != current_user:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    with get_db() as conn:
        conn.execute(
            "REPLACE INTO secrets (username, secret) VALUES (?, ?)",
            (request.username, request.secret)
        )
        conn.commit()
    return {"message": "Secret has been set successfully"}

@app.get("/get_secret")
async def get_secret(username: str = Query(...), current_user: Annotated[str, Depends(get_current_user)]):
    if username != current_user:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT secret FROM secrets WHERE username = ?", (username,))
        secret_row = cursor.fetchone()
        if not secret_row:
            return {"secret": ""}
        return {"secret": secret_row["secret"]}

# Run the app
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)