import sqlite3
import os
import jwt
from datetime import datetime, timedelta
from fastapi import FastAPI, Depends, HTTPException, status, Request
from pydantic import BaseModel, Field
from typing import Annotated
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from passlib.context import CryptContext

app = FastAPI()

# Global rate limiting data structure
request_counts = {}

# Initialize database
def init_db():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            username TEXT UNIQUE NOT NULL,
            hashed_password TEXT NOT NULL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS secrets (
            username TEXT PRIMARY KEY,
            secret TEXT NOT NULL,
            FOREIGN KEY (username) REFERENCES users(username)
        )
    ''')
    conn.commit()
    conn.close()

@app.on_event("startup")
def on_startup():
    init_db()
    if 'APP_SECRET' not in os.environ:
        raise RuntimeError("APP_SECRET environment variable is not set")

# Rate limiting helper
def rate_limit(key: str, max_requests: int, window_seconds: int):
    now = datetime.now()
    # Clean up expired entries
    to_remove = []
    for k in list(request_counts.keys()):
        if now - request_counts[k]['timestamp'] > timedelta(seconds=window_seconds):
            to_remove.append(k)
    for k in to_remove:
        del request_counts[k]
    
    if key in request_counts:
        if request_counts[key]['count'] >= max_requests:
            raise HTTPException(
                status_code=429, 
                detail="Too many requests, please try again later"
            )
        else:
            request_counts[key]['count'] += 1
            request_counts[key]['timestamp'] = now
    else:
        request_counts[key] = {'count': 1, 'timestamp': now}

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT settings
security = HTTPBearer()

# Dependency to get current user from token
def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, os.environ['APP_SECRET'], algorithms=['HS256'])
        username = payload.get('sub')
        if not username:
            raise HTTPException(status_code=401, detail='Invalid authentication token')
        return username
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail='Invalid authentication token')

# Models
class RegisterRequest(BaseModel):
    email: str
    username: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class SetSecretRequest(BaseModel):
    username: str
    secret: str

class SecretResponse(BaseModel):
    message: str

class GetSecretResponse(BaseModel):
    secret: str

# Endpoints
@app.post("/register", status_code=201)
async def register(request: RegisterRequest, req: Request):
    ip = req.client.host
    rate_limit(f"register:{ip}", max_requests=5, window_seconds=60)
    
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE email = ? OR username = ?", (request.email, request.username))
    if c.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Email already in use or invalid data")
    hashed_password = pwd_context.hash(request.password)
    try:
        c.execute("INSERT INTO users (email, username, hashed_password) VALUES (?, ?, ?)",
                  (request.email, request.username, hashed_password))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Email already in use or invalid data")
    conn.close()
    return {"message": "Registration successful"}

@app.post("/login")
async def login(request: LoginRequest, req: Request):
    ip = req.client.host
    rate_limit(f"login:{ip}", max_requests=5, window_seconds=60)
    
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT username, hashed_password FROM users WHERE email = ?", (request.email,))
    user = c.fetchone()
    conn.close()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    username, hashed_password = user
    if not pwd_context.verify(request.password, hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    payload = {
        'sub': username,
        'email': request.email,
        'exp': datetime.utcnow() + timedelta(hours=1)
    }
    token = jwt.encode(payload, os.environ['APP_SECRET'], algorithm='HS256')
    return {"token": token, "message": "Login successful"}

@app.post("/set_secret")
async def set_secret(
    request: SetSecretRequest,
    current_user: Annotated[str, Depends(get_current_user)],
    req: Request
):
    if len(request.secret) > 1024:
        raise HTTPException(status_code=413, detail="Secret too large")
    rate_limit(f"set_secret:{current_user}", max_requests=100, window_seconds=3600)
    
    if request.username != current_user:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("REPLACE INTO secrets (username, secret) VALUES (?, ?)",
              (request.username, request.secret))
    conn.commit()
    conn.close()
    return {"message": "Secret has been set successfully"}

@app.get("/get_secret")
async def get_secret(
    username: str,
    current_user: Annotated[str, Depends(get_current_user)],
    req: Request
):
    rate_limit(f"get_secret:{current_user}", max_requests=100, window_seconds=3600)
    
    if username != current_user:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT secret FROM secrets WHERE username = ?", (username,))
    result = c.fetchone()
    conn.close()
    if not result:
        return {"secret": ""}
    return {"secret": result[0]}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)