import sqlite3
import uuid
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
import os

app = FastAPI()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

DATABASE_URL = "db.sqlite3"

def init_db():
    with sqlite3.connect(DATABASE_URL, check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS invites (
                email TEXT NOT NULL,
                invite_id TEXT NOT NULL PRIMARY KEY,
                UNIQUE(email)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_name TEXT NOT NULL PRIMARY KEY,
                password_hash TEXT NOT NULL,
                invite_id TEXT NOT NULL
            )
        """)
        conn.commit()

init_db()

def get_db():
    db = sqlite3.connect(DATABASE_URL, check_same_thread=False)
    try:
        yield db
    finally:
        db.close()

class InviteUserRequest(BaseModel):
    email: EmailStr

class InviteUserResponse(BaseModel):
    invite_id: str
    message: str

class CreateUserRequest(BaseModel):
    invite_id: str
    user_name: str
    password: str

@app.post("/invite_user", response_model=InviteUserResponse)
def invite_user(request: InviteUserRequest, db: sqlite3.Connection = Depends(get_db)):
    email = request.email
    cursor = db.cursor()
    cursor.execute("SELECT invite_id FROM invites WHERE email = ?", (email,))
    existing = cursor.fetchone()
    if existing:
        return {"invite_id": existing[0], "message": "Existing invitation retrieved."}
    
    invite_id = str(uuid.uuid4())
    try:
        cursor.execute("INSERT INTO invites (email, invite_id) VALUES (?, ?)", (email, invite_id))
        db.commit()
    except sqlite3.IntegrityError:
        cursor.execute("SELECT invite_id FROM invites WHERE email = ?", (email,))
        existing = cursor.fetchone()
        if existing:
            return {"invite_id": existing[0], "message": "Existing invitation retrieved."}
        raise HTTPException(status_code=500, detail={"message": "Database error"})
    
    return {"invite_id": invite_id, "message": "Invitation created."}

@app.post("/create_user")
def create_user(request: CreateUserRequest, db: sqlite3.Connection = Depends(get_db)):
    invite_id = request.invite_id
    user_name = request.user_name
    password = request.password
    
    cursor = db.cursor()
    cursor.execute("SELECT email FROM invites WHERE invite_id = ?", (invite_id,))
    invite = cursor.fetchone()
    if not invite:
        raise HTTPException(status_code=401, detail={"message": "Invalid invite_id"})
    
    cursor.execute("DELETE FROM invites WHERE invite_id = ?", (invite_id,))
    cursor.execute("SELECT user_name FROM users WHERE user_name = ?", (user_name,))
    if cursor.fetchone():
        raise HTTPException(status_code=400, detail={"message": "Username already exists. Try providing different username."})
    
    hashed_password = pwd_context.hash(password)
    try:
        cursor.execute("INSERT INTO users (user_name, password_hash, invite_id) VALUES (?, ?, ?)",
                       (user_name, hashed_password, invite_id))
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail={"message": "Internal server error"})
    
    return {"message": "User created"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)