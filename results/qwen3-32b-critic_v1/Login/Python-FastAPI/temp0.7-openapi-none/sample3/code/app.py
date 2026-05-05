import os
import sqlite3
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, EmailStr
import jwt
from passlib.hash import bcrypt
from datetime import datetime, timedelta
import time

app = FastAPI()

# Initialize database
conn = sqlite3.connect("db.sqlite3")
cursor = conn.cursor()
cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        email TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        password_hash TEXT NOT NULL
    )
""")
conn.commit()
conn.close()

class RegisterModel(BaseModel):
    email: EmailStr
    password: str
    name: str

class LoginModel(BaseModel):
    email: EmailStr
    password: str

# Simple in-memory rate limiter
request_count = {}
RATE_LIMIT = 5  # requests per minute
RATE_LIMIT_WINDOW = 60  # seconds

def check_rate_limit(ip):
    now = time.time()
    to_remove = []
    for ip_key in list(request_count.keys()):
        if now - request_count[ip_key]["timestamp"] > RATE_LIMIT_WINDOW:
            to_remove.append(ip_key)
    for ip_key in to_remove:
        del request_count[ip_key]
    if ip in request_count:
        if request_count[ip]["count"] >= RATE_LIMIT:
            raise HTTPException(status_code=429, detail="Too many requests, please try again later")
        else:
            request_count[ip]["count"] += 1
            request_count[ip]["timestamp"] = now
    else:
        request_count[ip] = {"count": 1, "timestamp": now}

@app.on_event("startup")
async def validate_app_secret():
    if not os.environ.get("APP_SECRET"):
        raise ValueError("APP_SECRET environment variable is missing")
    if len(os.environ["APP_SECRET"]) < 16:
        raise ValueError("APP_SECRET is too short")

@app.post("/register")
def register_user(user: RegisterModel, request: Request):
    ip = request.client.host
    check_rate_limit(ip)
    conn = None
    try:
        conn = sqlite3.connect("db.sqlite3")
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE email = ?", (user.email,))
        existing = cursor.fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="Registration failed")
        password_hash = bcrypt.hash(user.password)
        cursor.execute(
            "INSERT INTO users (email, name, password_hash) VALUES (?, ?, ?)",
            (user.email, user.name, password_hash)
        )
        conn.commit()
        return {"message": "Registration successful"}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Registration failed")
    except Exception as e:
        raise HTTPException(status_code=400, detail="Registration failed")
    finally:
        if conn:
            conn.close()

@app.post("/login")
def login_user(user: LoginModel, request: Request):
    ip = request.client.host
    check_rate_limit(ip)
    conn = None
    try:
        conn = sqlite3.connect("db.sqlite3")
        cursor = conn.cursor()
        cursor.execute("SELECT password_hash FROM users WHERE email = ?", (user.email,))
        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=401, detail="Invalid email or password")
        password_hash = result[0]
        if not bcrypt.verify(user.password, password_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        payload = {
            "email": user.email,
            "exp": datetime.utcnow() + timedelta(hours=1)
        }
        token = jwt.encode(payload, os.environ["APP_SECRET"], algorithm="HS256")
        return {"token": token, "message": "Login successful"}
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Internal server error")
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)