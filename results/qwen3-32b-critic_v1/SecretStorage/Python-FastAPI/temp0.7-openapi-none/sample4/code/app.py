import sqlite3
import os
from fastapi import FastAPI, Depends, HTTPException, Header, Query, Body
from pydantic import BaseModel, Field, EmailStr
import jwt
from datetime import datetime, timedelta
from passlib.hash import bcrypt
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise ValueError("APP_SECRET environment variable is not set")

def get_db():
    return sqlite3.connect('db.sqlite3')

@app.on_event("startup")
def create_tables():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS secrets (
            username TEXT PRIMARY KEY,
            secret TEXT NOT NULL,
            FOREIGN KEY (username) REFERENCES users (username)
        )
    ''')
    conn.commit()
    conn.close()

class UserRegister(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=20)
    password: str = Field(..., min_length=8)

class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(...)

class SetSecret(BaseModel):
    username: str
    secret: str = Field(..., max_length=1024)

def get_token(authorization: str = Header(..., description="Bearer token")):
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    return authorization[7:]

def get_current_user(token: str = Depends(get_token)):
    try:
        payload = jwt.decode(
            token,
            APP_SECRET,
            algorithms=["HS256"],
            options={"require_exp": True}
        )
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Invalid authentication token")
        return username
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail="Invalid authentication token")

@app.post("/register", status_code=201)
async def register(user_data: UserRegister):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM users WHERE email = ? OR username = ?", 
                       (user_data.email, user_data.username))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Email already in use or invalid data")
        password_hash = bcrypt.hash(user_data.password)
        cursor.execute("INSERT INTO users (email, username, password_hash) VALUES (?, ?, ?)",
                       (user_data.email, user_data.username, password_hash))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Email already in use or invalid data")
    finally:
        conn.close()
    return {"message": "Registration successful"}

@app.post("/login")
async def login(user_data: UserLogin):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT username, password_hash FROM users WHERE email = ?", (user_data.email,))
    result = cursor.fetchone()
    conn.close()
    if not result:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    username, password_hash = result
    if not bcrypt.verify(user_data.password, password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    payload = {
        "sub": username,
        "exp": datetime.utcnow() + timedelta(minutes=30)
    }
    token = jwt.encode(payload, APP_SECRET, algorithm="HS256")
    return {"token": token, "message": "Login successful"}

@app.post("/set_secret")
async def set_secret(
    data: SetSecret,
    current_user: str = Depends(get_current_user)
):
    if data.username != current_user:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT OR REPLACE INTO secrets (username, secret) VALUES (?, ?)",
                       (data.username, data.secret))
        conn.commit()
    finally:
        conn.close()
    return {"message": "Secret has been set successfully"}

@app.get("/get_secret")
async def get_secret(
    username: str = Query(..., description="Username to retrieve the secret for"),
    current_user: str = Depends(get_current_user)
):
    if username != current_user:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT secret FROM secrets WHERE username = ?", (username,))
    result = cursor.fetchone()
    conn.close()
    if not result:
        return {"secret": ""}
    return {"secret": result[0]}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)