import sqlite3
import uuid
import time
import os
from fastapi import FastAPI, HTTPException, Request, JSONResponse
from pydantic import BaseModel, EmailStr, Field
from passlib.context import CryptContext
from collections import defaultdict

app = FastAPI()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def init_db():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS invites
                 (email TEXT PRIMARY KEY, invite_id TEXT, used INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (username TEXT PRIMARY KEY, password_hash TEXT, invite_id TEXT)''')
    conn.commit()
    conn.close()

@app.on_event("startup")
def on_startup():
    init_db()
    app.state.request_counts = defaultdict(list)

class InviteUserRequest(BaseModel):
    email: EmailStr

class InviteUserResponse(BaseModel):
    invite_id: str
    message: str

class CreateUserRequest(BaseModel):
    invite_id: str
    user_name: str = Field(..., max_length=50)
    password: str = Field(..., max_length=100)

class CreateUserResponse(BaseModel):
    message: str

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = request.client.host
    now = time.time()
    RATE_LIMIT = 10  # 10 requests per minute
    TIME_WINDOW = 60  # seconds
    
    if request.url.path in ["/invite_user", "/create_user"]:
        # Clean up old entries
        request_counts = app.state.request_counts
        request_counts[client_ip] = [t for t in request_counts[client_ip] if now - t < TIME_WINDOW]
        
        if len(request_counts[client_ip]) >= RATE_LIMIT:
            return JSONResponse(
                status_code=429,
                content={"message": "Too many requests. Please try again later."}
            )
        
        request_counts[client_ip].append(now)
    
    response = await call_next(request)
    return response

@app.post("/invite_user", response_model=InviteUserResponse)
async def invite_user(request: InviteUserRequest):
    email = request.email
    conn = sqlite3.connect('db.sqlite3')
    try:
        c = conn.cursor()
        c.execute("SELECT invite_id FROM invites WHERE email = ?", (email,))
        result = c.fetchone()
        if result:
            invite_id = result[0]
            message = "Existing invitation retrieved."
        else:
            invite_id = str(uuid.uuid4())
            c.execute("INSERT INTO invites (email, invite_id) VALUES (?, ?)", (email, invite_id))
            message = "New invitation created."
        conn.commit()
    finally:
        conn.close()
    return {"invite_id": invite_id, "message": message}

@app.post("/create_user", response_model=CreateUserResponse)
async def create_user(request: CreateUserRequest):
    invite_id = request.invite_id
    username = request.user_name
    password = request.password
    
    conn = sqlite3.connect('db.sqlite3')
    try:
        c = conn.cursor()
        
        c.execute("SELECT email FROM invites WHERE invite_id = ? AND used = 0", (invite_id,))
        if not c.fetchone():
            raise HTTPException(status_code=401, detail={"message": "Invalid invite_id"})
        
        c.execute("SELECT username FROM users WHERE username = ?", (username,))
        if c.fetchone():
            raise HTTPException(status_code=400, detail={"message": "Username already exists. Try providing different username."})
        
        password_hash = pwd_context.hash(password)
        
        c.execute("INSERT INTO users (username, password_hash, invite_id) VALUES (?, ?, ?)", 
                  (username, password_hash, invite_id))
        c.execute("UPDATE invites SET used = 1 WHERE invite_id = ?", (invite_id,))
        conn.commit()
    finally:
        conn.close()
    return {"message": "User created"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)