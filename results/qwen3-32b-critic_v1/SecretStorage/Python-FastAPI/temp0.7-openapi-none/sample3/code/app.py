import os
import sqlite3
import datetime
from fastapi import FastAPI, Depends, HTTPException, status, Body, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime, timedelta
import jwt
from passlib.context import CryptContext
from jose import jwe, jwt as jose_jwt, exceptions as jose_exceptions

app = FastAPI()

# Setup security
security = HTTPBearer()

# Setup password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT settings
SECRET_KEY = os.getenv("APP_SECRET")
if not SECRET_KEY:
    raise ValueError("APP_SECRET environment variable must be set and non-empty")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Database setup
def get_db():
    db = sqlite3.connect('db.sqlite3', check_same_thread=False)
    db.row_factory = sqlite3.Row
    return db

# Initialize database
def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS secrets (
                user_id INTEGER NOT NULL PRIMARY KEY,
                secret TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS login_attempts (
                email TEXT NOT NULL,
                attempts INTEGER DEFAULT 0,
                last_attempt TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (email)
            )
        """)
        db.commit()

init_db()

# Helper functions
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jose_jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jose_jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        email: str = payload.get("email")
        if username is None or email is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        return {"username": username, "email": email}
    except jose_exceptions.JWSError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

# Models
class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(max_length=50)
    password: str = Field(max_length=128)

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)

class SetSecretRequest(BaseModel):
    username: str = Field(max_length=50)
    secret: str = Field(max_length=1024)

class SecretResponse(BaseModel):
    secret: str

# Endpoints

@app.post("/register", status_code=201)
def register(user: RegisterRequest):
    db = get_db()
    try:
        existing_user = db.execute("SELECT * FROM users WHERE email = ? OR username = ?", 
                                  (user.email, user.username)).fetchone()
        if existing_user:
            if existing_user['email'] == user.email:
                raise HTTPException(status_code=400, detail="Email already in use")
            else:
                raise HTTPException(status_code=400, detail="Username already taken")
        
        password_hash = get_password_hash(user.password)
        db.execute("INSERT INTO users (email, username, password_hash) VALUES (?, ?, ?)", 
                  (user.email, user.username, password_hash))
        db.commit()
        return {"message": "Registration successful"}
    except sqlite3.IntegrityError as e:
        error_msg = str(e)
        if "users.email" in error_msg:
            raise HTTPException(status_code=400, detail="Email already in use")
        elif "users.username" in error_msg:
            raise HTTPException(status_code=400, detail="Username already taken")
        else:
            raise HTTPException(status_code=400, detail="Invalid data")
    finally:
        db.close()

@app.post("/login")
def login(user: LoginRequest):
    db = get_db()
    try:
        current_time = datetime.utcnow()
        login_attempt = db.execute("SELECT attempts, last_attempt FROM login_attempts WHERE email = ?", (user.email,)).fetchone()
        if login_attempt:
            attempts = login_attempt['attempts']
            last_attempt = login_attempt['last_attempt']
            if isinstance(last_attempt, str):
                last_attempt_dt = datetime.strptime(last_attempt, '%Y-%m-%d %H:%M:%S.%f')
            else:
                last_attempt_dt = last_attempt
            time_diff = (current_time - last_attempt_dt).total_seconds() / 60  # minutes
            if attempts >= 5 and time_diff < 15:
                raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")
        
        db_user = db.execute("SELECT * FROM users WHERE email = ?", (user.email,)).fetchone()
        if not db_user or not verify_password(user.password, db_user['password_hash']):
            db.execute("""
                INSERT INTO login_attempts (email, attempts, last_attempt)
                VALUES (?, 1, ?)
                ON CONFLICT(email) DO UPDATE SET
                    attempts = attempts + 1,
                    last_attempt = ?
            """, (user.email, current_time, current_time))
            db.commit()
            raise HTTPException(status_code=401, detail="Invalid email or password")
        
        db.execute("DELETE FROM login_attempts WHERE email = ?", (user.email,))
        db.commit()
        
        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": db_user['username'], "email": db_user['email']},
            expires_delta=access_token_expires
        )
        return {"token": access_token, "message": "Login successful"}
    finally:
        db.close()

@app.post("/set_secret")
def set_secret(
    request: SetSecretRequest,
    current_user: dict = Depends(get_current_user)
):
    db = get_db()
    try:
        if request.username != current_user["username"]:
            raise HTTPException(status_code=401, detail="Invalid authentication token")
        
        user = db.execute("SELECT id FROM users WHERE username = ?", (request.username,)).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        try:
            encrypted_secret = jwe.encrypt(
                request.secret.encode(), 
                SECRET_KEY.encode(), 
                algorithm='A256KW', 
                encryption='A256GCM'
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail="Error encrypting secret")
        
        db.execute("INSERT OR REPLACE INTO secrets (user_id, secret) VALUES (?, ?)", 
                  (user['id'], encrypted_secret.decode()))
        db.commit()
        return {"message": "Secret has been set successfully"}
    finally:
        db.close()

@app.get("/get_secret")
def get_secret(
    username: str = Query(...),
    current_user: dict = Depends(get_current_user)
):
    db = get_db()
    try:
        if username != current_user["username"]:
            raise HTTPException(status_code=401, detail="Invalid authentication token")
        
        secret_row = db.execute("SELECT secret FROM secrets WHERE user_id = (SELECT id FROM users WHERE username = ?)", 
                           (username,)).fetchone()
        if not secret_row:
            return {"secret": ""}
        
        encrypted_secret = secret_row['secret']
        try:
            decrypted_secret = jwe.decrypt(
                encrypted_secret, 
                SECRET_KEY.encode(), 
                algorithms=['A256KW'], 
                encryption='A256GCM'
            ).decode()
        except Exception as e:
            return {"secret": ""}
        
        return {"secret": decrypted_secret}
    finally:
        db.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)