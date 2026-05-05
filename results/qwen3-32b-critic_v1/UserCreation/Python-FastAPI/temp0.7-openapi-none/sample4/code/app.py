from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel, EmailStr, field_validator
import sqlite3
import uuid
from passlib.context import CryptContext
import os
import re

app = FastAPI()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
MAX_INVITES = 1000  # Global cap on total invitations

def get_db_connection():
    conn = sqlite3.connect('db.sqlite3')
    conn.row_factory = sqlite3.Row
    return conn

@app.on_event("startup")
def create_tables():
    conn = get_db_connection()
    conn.execute('''CREATE TABLE IF NOT EXISTS invites 
                   (email TEXT PRIMARY KEY, invite_id TEXT UNIQUE NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS users 
                   (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, invite_id TEXT NOT NULL)''')
    conn.commit()
    conn.close()

class InviteUserRequest(BaseModel):
    email: EmailStr

class InviteUserResponse(BaseModel):
    invite_id: str
    message: str

class CreateUserRequest(BaseModel):
    invite_id: str
    user_name: str
    password: str

    @field_validator('user_name')
    def validate_username(cls, v):
        if len(v) < 3 or len(v) > 30:
            raise ValueError("Username must be between 3 and 30 characters")
        if not re.match(r"^[a-zA-Z0-9_-]+$", v):
            raise ValueError("Username can only contain letters, numbers, underscores, and hyphens")
        return v

class CreateUserResponse(BaseModel):
    message: str

class ErrorModel(BaseModel):
    message: str

@app.post("/invite_user", response_model=InviteUserResponse)
async def invite_user(request: InviteUserRequest):
    email = request.email
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT invite_id FROM invites WHERE email = ?", (email,))
    row = cursor.fetchone()
    
    if row:
        invite_id = row['invite_id']
        message = "Existing invitation retrieved."
    else:
        # Check global invite limit
        cursor.execute("SELECT COUNT(*) as count FROM invites")
        count_row = cursor.fetchone()
        if count_row['count'] >= MAX_INVITES:
            conn.close()
            raise HTTPException(status_code=400, detail={"message": "Maximum number of invites reached"})
        
        invite_id = str(uuid.uuid4())
        cursor.execute("INSERT INTO invites (email, invite_id) VALUES (?, ?)", (email, invite_id))
        message = "New invitation created."
    
    conn.commit()
    conn.close()
    return {"invite_id": invite_id, "message": message}

@app.post("/create_user", response_model=CreateUserResponse, responses={
    400: {"model": ErrorModel},
    401: {"model": ErrorModel}
})
async def create_user(request: CreateUserRequest):
    invite_id = request.invite_id
    username = request.user_name
    password = request.password
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if username exists
    cursor.execute("SELECT username FROM users WHERE username = ?", (username,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail={"message": "Username already exists. Try providing different username."})
    
    # Check if invite exists
    cursor.execute("SELECT email FROM invites WHERE invite_id = ?", (invite_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=401, detail={"message": "Invalid invite_id"})
    
    # Hash password
    password_hash = pwd_context.hash(password)
    
    # Create user and consume invite
    cursor.execute("INSERT INTO users (username, password_hash, invite_id) VALUES (?, ?, ?)", 
                  (username, password_hash, invite_id))
    cursor.execute("DELETE FROM invites WHERE invite_id = ?", (invite_id,))
    
    conn.commit()
    conn.close()
    
    return {"message": "User created"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)