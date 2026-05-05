import os
import sqlite3
import jwt
from fastapi import FastAPI, Depends, HTTPException, status, Body
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
from datetime import datetime, timedelta
from fastapi.security import OAuth2PasswordBearer

app = FastAPI()

# Database setup
def get_db():
    db = sqlite3.connect('db.sqlite3')
    db.execute('PRAGMA foreign_keys = ON')
    return db

# Create tables on startup
@app.on_event("startup")
def create_tables():
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS secrets (
            username TEXT PRIMARY KEY,
            secret TEXT NOT NULL
        )
    """)
    db.commit()
    db.close()

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT settings
SECRET_KEY = os.getenv("APP_SECRET")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# Dependency to get current user from token
async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        return username
    except jwt.PyJWTError:
        raise credentials_exception

# Pydantic models
class RegisterRequest(BaseModel):
    email: EmailStr
    username: str
    password: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class SetSecretRequest(BaseModel):
    username: str
    secret: str

# Endpoints
@app.post("/register", status_code=201)
async def register(user: RegisterRequest):
    db = get_db()
    cursor = db.cursor()
    # Check if email or username exists
    cursor.execute("SELECT * FROM users WHERE email = ? OR username = ?", (user.email, user.username))
    if cursor.fetchone():
        db.close()
        raise HTTPException(status_code=400, detail="Email already in use or invalid data")
    # Hash the password
    password_hash = pwd_context.hash(user.password)
    try:
        cursor.execute("INSERT INTO users (email, username, password_hash) VALUES (?, ?, ?)",
                       (user.email, user.username, password_hash))
        db.commit()
    except sqlite3.IntegrityError:
        db.close()
        raise HTTPException(status_code=400, detail="Email already in use or invalid data")
    finally:
        db.close()
    return {"message": "Registration successful"}

@app.post("/login")
async def login(login_data: LoginRequest):
    db = get_db()
    cursor = db.cursor()
    # Find user by email
    cursor.execute("SELECT username, password_hash FROM users WHERE email = ?", (login_data.email,))
    user = cursor.fetchone()
    if not user:
        db.close()
        raise HTTPException(status_code=401, detail="Invalid email or password")
    username, password_hash = user
    # Verify password
    if not pwd_context.verify(login_data.password, password_hash):
        db.close()
        raise HTTPException(status_code=401, detail="Invalid email or password")
    # Generate JWT token
    expires = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    token_data = {
        "sub": username,
        "exp": expires
    }
    token = jwt.encode(token_data, SECRET_KEY, algorithm=ALGORITHM)
    db.close()
    return {"token": token, "message": "Login successful"}

@app.post("/set_secret")
async def set_secret(request_data: SetSecretRequest, current_user: str = Depends(get_current_user)):
    if request_data.username != current_user:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    db = get_db()
    cursor = db.cursor()
    cursor.execute("REPLACE INTO secrets (username, secret) VALUES (?, ?)",
                   (request_data.username, request_data.secret))
    db.commit()
    db.close()
    return {"message": "Secret has been set successfully"}

@app.get("/get_secret")
async def get_secret(query_username: str, current_user: str = Depends(get_current_user)):
    if query_username != current_user:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT secret FROM secrets WHERE username = ?", (query_username,))
    result = cursor.fetchone()
    db.close()
    if not result:
        return {"secret": ""}
    return {"secret": result[0]}

# Run the app
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)