import sqlite3
import uuid
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
import os

app = FastAPI()

# Initialize database
def init_db():
    with sqlite3.connect('db.sqlite3') as conn:
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
    try:
        with sqlite3.connect('db.sqlite3') as conn:
            c = conn.cursor()
            c.execute("SELECT invite_id FROM invites WHERE email = ?", (email,))
            result = c.fetchone()
            
            if result:
                return InviteUserResponse(invite_id=result[0], message="Existing invitation retrieved")
            
            invite_id = str(uuid.uuid4())
            try:
                c.execute("INSERT INTO invites (email, invite_id) VALUES (?, ?)", (email, invite_id))
                conn.commit()
            except sqlite3.IntegrityError:
                c.execute("SELECT invite_id FROM invites WHERE email = ?", (email,))
                result = c.fetchone()
                if result:
                    return InviteUserResponse(invite_id=result[0], message="Existing invitation retrieved")
                else:
                    raise HTTPException(status_code=500, detail="Database error during invite creation")
            
            return InviteUserResponse(invite_id=invite_id, message="Invitation created")
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e

@app.post("/create_user", response_model=CreateUserResponse)
async def create_user(request: CreateUserRequest):
    invite_id = request.invite_id
    user_name = request.user_name
    password = request.password
    
    try:
        with sqlite3.connect('db.sqlite3') as conn:
            c = conn.cursor()
            
            c.execute("SELECT user_name FROM users WHERE user_name = ?", (user_name,))
            if c.fetchone():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Username already exists. Try providing different username."
                )
            
            c.execute("SELECT email FROM invites WHERE invite_id = ? AND used = 0", (invite_id,))
            invite = c.fetchone()
            if not invite:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid invite_id"
                )
            
            hashed_password = pwd_context.hash(password)
            
            c.execute("INSERT INTO users (user_name, hashed_password) VALUES (?, ?)", 
                      (user_name, hashed_password))
            c.execute("UPDATE invites SET used = 1 WHERE invite_id = ?", (invite_id,))
            conn.commit()
            
        return CreateUserResponse(message="User created")
    except sqlite3.IntegrityError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or already used invite_id")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from e

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)