import os
import jwt
from datetime import datetime, timedelta
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
import sqlite3
from passlib.context import CryptContext

app = FastAPI()

# Security schemes
security_bearer = HTTPBearer()

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT settings
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise ValueError("APP_SECRET environment variable is not set")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60  # 1 hour

# Rate limiting for login
login_attempts = {}

# Dependency to get current user from JWT
def get_current_user(token: HTTPAuthorizationCredentials = Depends(security_bearer)):
    try:
        payload = jwt.decode(token.credentials, APP_SECRET, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Invalid authentication token")
        return username
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication token")

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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            secret TEXT NOT NULL,
            FOREIGN KEY (username) REFERENCES users (username)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# Password hashing functions
def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)

# Models
class RegisterRequest(BaseModel):
    email: str
    username: str = Field(..., pattern=r'^[\w-]{3,20}$')  # Username format validation
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class SetSecretRequest(BaseModel):
    username: str
    secret: str = Field(..., max_length=1024)  # Secret size limit

class GetSecretResponse(BaseModel):
    secret: str

# Endpoints
@app.post("/register", status_code=201)
async def register(data: RegisterRequest):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    try:
        c.execute("SELECT * FROM users WHERE email = ? OR username = ?", (data.email, data.username))
        if c.fetchone():
            raise HTTPException(status_code=400, detail="Email already in use or invalid data")
        hashed_password = hash_password(data.password)
        c.execute("INSERT INTO users (email, username, hashed_password) VALUES (?, ?, ?)", 
                  (data.email, data.username, hashed_password))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Email already in use or invalid data")
    finally:
        conn.close()
    return {"message": "Registration successful"}

@app.post("/login")
async def login(data: LoginRequest):
    email = data.email
    # Rate limiting
    now = datetime.utcnow()
    if email in login_attempts:
        attempts, timestamp = login_attempts[email]
        if (now - timestamp) < timedelta(minutes=1):
            if attempts >= 5:
                raise HTTPException(status_code=429, detail="Too many login attempts")
            login_attempts[email] = (attempts + 1, timestamp)
    else:
        login_attempts[email] = (1, now)
    
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT username, hashed_password FROM users WHERE email = ?", (email,))
    user = c.fetchone()
    conn.close()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    username, hashed_password = user
    if not verify_password(data.password, hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": username,
        "exp": now + access_token_expires
    }
    token = jwt.encode(payload, APP_SECRET, algorithm=ALGORITHM)
    return {"token": token, "message": "Login successful"}

@app.post("/set_secret")
async def set_secret(data: SetSecretRequest, current_user: str = Depends(get_current_user)):
    if current_user != data.username:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    try:
        c.execute("REPLACE INTO secrets (username, secret) VALUES (?, ?)", (data.username, data.secret))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Error setting secret")
    finally:
        conn.close()
    return {"message": "Secret has been set successfully"}

@app.get("/get_secret")
async def get_secret(username: str = Query(...), current_user: str = Depends(get_current_user)):
    if current_user != username:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT secret FROM secrets WHERE username = ?", (username,))
    result = c.fetchone()
    conn.close()
    if not result:
        raise HTTPException(status_code=401, detail="Secret not found")
    return {"secret": result[0]}

# Run the app
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)