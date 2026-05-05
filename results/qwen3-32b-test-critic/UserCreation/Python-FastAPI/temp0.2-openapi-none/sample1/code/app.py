import os
import sqlite3
import uuid
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel
from passlib.context import CryptContext

app = FastAPI()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class InviteUserRequest(BaseModel):
    email: str

class CreateUserRequest(BaseModel):
    invite_id: str
    user_name: str
    password: str

def get_db():
    db = sqlite3.connect('db.sqlite3')
    db.execute('''
        CREATE TABLE IF NOT EXISTS invitations (
            email TEXT PRIMARY KEY,
            invite_id TEXT NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            hashed_password TEXT NOT NULL
        )
    ''')
    return db

@app.post("/invite_user")
async def invite_user(request: InviteUserRequest):
    email = request.email
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT invite_id FROM invitations WHERE email = ?", (email,))
    result = cursor.fetchone()
    if result:
        invite_id = result[0]
    else:
        invite_id = str(uuid.uuid4())
        cursor.execute("INSERT INTO invitations (email, invite_id) VALUES (?, ?)", (email, invite_id))
        db.commit()
    db.close()
    return {"invite_id": invite_id, "message": "Invitation created or retrieved"}

@app.post("/create_user")
async def create_user(request: CreateUserRequest):
    invite_id = request.invite_id
    user_name = request.user_name
    password = request.password
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT email FROM invitations WHERE invite_id = ?", (invite_id,))
    result = cursor.fetchone()
    if not result:
        db.close()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid invite_id")
    cursor.execute("SELECT username FROM users WHERE username = ?", (user_name,))
    if cursor.fetchone():
        db.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already exists. Try providing different username.")
    hashed_password = pwd_context.hash(password)
    cursor.execute("INSERT INTO users (username, hashed_password) VALUES (?, ?)", (user_name, hashed_password))
    cursor.execute("DELETE FROM invitations WHERE invite_id = ?", (invite_id,))
    db.commit()
    db.close()
    return {"message": "User created"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)