import os
import sqlite3
from datetime import datetime, timedelta
from fastapi import FastAPI, Depends, HTTPException, Header
from pydantic import BaseModel
import jwt
from passlib.context import CryptContext

app = FastAPI()

# Pydantic models for request bodies
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

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT configuration
SECRET_KEY = os.getenv("APP_SECRET")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication token")

async def get_current_user(authorization: str = Header(..., description="Bearer token")):
    token = authorization.split(" ")[1]
    payload = decode_token(token)
    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=401, detail="Invalid token")
    return username

def get_db():
    return sqlite3.connect('db.sqlite3', check_same_thread=False)

@app.on_event("startup")
def on_startup():
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
            secret_text TEXT NOT NULL
        )
    """)
    db.commit()
    db.close()

@app.post("/register")
async def register(request: RegisterRequest):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ? OR username = ?", 
                  (request.email, request.username))
    existing_user = cursor.fetchone()
    if existing_user:
        db.close()
        raise HTTPException(status_code=400, detail="Email already in use or invalid data")
    
    password_hash = pwd_context.hash(request.password)
    cursor.execute("INSERT INTO users (email, username, password_hash) VALUES (?, ?, ?)",
                  (request.email, request.username, password_hash))
    db.commit()
    db.close()
    return {"message": "Registration successful"}

@app.post("/login")
async def login(request: LoginRequest):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT username, password_hash FROM users WHERE email = ?", (request.email,))
    user = cursor.fetchone()
    db.close()
    
    if not user or not pwd_context.verify(request.password, user[1]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    token_data = {"sub": user[0], "email": request.email}
    token = create_access_token(token_data)
    return {"token": token, "message": "Login successful"}

@app.post("/set_secret")
async def set_secret(request: SetSecretRequest, current_user: str = Depends(get_current_user)):
    if current_user != request.username:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute("INSERT OR REPLACE INTO secrets (username, secret_text) VALUES (?, ?)",
                  (request.username, request.secret))
    db.commit()
    db.close()
    return {"message": "Secret has been set successfully"}

@app.get("/get_secret")
async def get_secret(username: str, current_user: str = Depends(get_current_user)):
    if current_user != username:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT secret_text FROM secrets WHERE username = ?", (username,))
    result = cursor.fetchone()
    db.close()
    
    if result:
        return {"secret": result[0]}
    return {"secret": ""}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)