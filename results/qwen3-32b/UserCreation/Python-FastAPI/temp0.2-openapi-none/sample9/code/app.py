from fastapi import FastAPI, HTTPException
import sqlite3
import secrets
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from typing import Optional

app = FastAPI()

DB_NAME = 'db.sqlite3'

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

@app.on_event("startup")
def create_tables():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS invitations (
            email TEXT NOT NULL,
            invite_id TEXT PRIMARY KEY
        )
    ''')
    cursor.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_invitations_email ON invitations(email)
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

class InviteUserRequest(BaseModel):
    email: EmailStr

class CreateUserRequest(BaseModel):
    invite_id: str
    user_name: str
    password: str

@app.post("/invite_user")
async def invite_user(request: InviteUserRequest):
    email = request.email
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT invite_id FROM invitations WHERE email = ?", (email,))
    row = cursor.fetchone()
    if row:
        conn.close()
        return {"invite_id": row['invite_id'], "message": "Existing invitation retrieved."}
    invite_id = secrets.token_urlsafe(16)
    try:
        cursor.execute("INSERT INTO invitations (email, invite_id) VALUES (?, ?)", (email, invite_id))
        conn.commit()
    except sqlite3.IntegrityError:
        cursor.execute("SELECT invite_id FROM invitations WHERE email = ?", (email,))
        row = cursor.fetchone()
        if row:
            invite_id = row['invite_id']
        else:
            conn.close()
            raise HTTPException(status_code=500, detail="Database error")
    conn.close()
    return {"invite_id": invite_id, "message": "Invitation created."}

@app.post("/create_user")
async def create_user(request: CreateUserRequest):
    invite_id = request.invite_id
    user_name = request.user_name
    password = request.password
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM invitations WHERE invite_id = ?", (invite_id,))
    invite_row = cursor.fetchone()
    if not invite_row:
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid invite_id")
    cursor.execute("SELECT * FROM users WHERE username = ?", (user_name,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Username already exists. Try providing different username.")
    password_hash = pwd_context.hash(password)
    try:
        cursor.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (user_name, password_hash))
        cursor.execute("DELETE FROM invitations WHERE invite_id = ?", (invite_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=500, detail="Database error") from e
    conn.close()
    return {"message": "User created"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)