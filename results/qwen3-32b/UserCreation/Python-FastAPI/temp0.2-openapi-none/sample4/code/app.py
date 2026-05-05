import sqlite3
import uuid
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
import os

app = FastAPI()

# Initialize database
def init_db():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS invites (
            email TEXT PRIMARY KEY,
            invite_id TEXT NOT NULL,
            used INTEGER NOT NULL DEFAULT 0
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_name TEXT PRIMARY KEY,
            hashed_password TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Models
class InviteUserRequest(BaseModel):
    email: EmailStr

class InviteUserResponse(BaseModel):
    invite_id: str
    message: str

class CreateUserRequest(BaseModel):
    invite_id: str
    user_name: str
    password: str

class CreateUserResponse(BaseModel):
    message: str

# Endpoints
@app.post("/invite_user", response_model=InviteUserResponse)
async def invite_user(request: InviteUserRequest):
    email = request.email
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    
    # Check if email already has an invite
    c.execute("SELECT invite_id FROM invites WHERE email = ?", (email,))
    result = c.fetchone()
    
    if result:
        invite_id = result[0]
        conn.close()
        return {"invite_id": invite_id, "message": "Existing invitation retrieved"}
    
    # Generate new invite
    invite_id = str(uuid.uuid4())
    c.execute("INSERT INTO invites (email, invite_id) VALUES (?, ?)", (email, invite_id))
    conn.commit()
    conn.close()
    
    return {"invite_id": invite_id, "message": "Invitation created"}

@app.post("/create_user", response_model=CreateUserResponse)
async def create_user(request: CreateUserRequest):
    invite_id = request.invite_id
    user_name = request.user_name
    password = request.password
    
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    
    # Check if username exists
    c.execute("SELECT user_name FROM users WHERE user_name = ?", (user_name,))
    if c.fetchone():
        conn.close()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists. Try providing different username."
        )
    
    # Validate invite
    c.execute("SELECT email FROM invites WHERE invite_id = ? AND used = 0", (invite_id,))
    invite = c.fetchone()
    if not invite:
        conn.close()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid invite_id"
        )
    
    # Hash password
    hashed_password = pwd_context.hash(password)
    
    # Create user and mark invite as used
    c.execute("INSERT INTO users (user_name, hashed_password) VALUES (?, ?)", 
              (user_name, hashed_password))
    c.execute("UPDATE invites SET used = 1 WHERE invite_id = ?", (invite_id,))
    conn.commit()
    conn.close()
    
    return {"message": "User created"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)