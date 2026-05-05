import sqlite3
import uuid
from fastapi import FastAPI, HTTPException, Body
import os
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

app = FastAPI()

def get_db():
    db = sqlite3.connect('db.sqlite3')
    db.execute('PRAGMA foreign_keys = 1')
    return db

@app.on_event("startup")
def create_tables():
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS invites (
            email TEXT PRIMARY KEY,
            invite_id TEXT NOT NULL,
            used BOOLEAN NOT NULL DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_name TEXT PRIMARY KEY,
            password TEXT NOT NULL
        )
    ''')
    db.commit()
    db.close()

@app.post("/invite_user")
async def invite_user(email: str = Body(..., embed=True)):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT invite_id, used FROM invites WHERE email = ?", (email,))
    result = cursor.fetchone()
    if result:
        invite_id, used = result
        message = "Invitation already exists"
    else:
        invite_id = str(uuid.uuid4())
        cursor.execute("INSERT INTO invites (email, invite_id, used) VALUES (?, ?, 0)", (email, invite_id))
        db.commit()
        message = "Invitation created"
    db.close()
    return {"invite_id": invite_id, "message": message}

@app.post("/create_user")
async def create_user(invite_id: str = Body(...), user_name: str = Body(...), password: str = Body(...)):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT email, used FROM invites WHERE invite_id = ?", (invite_id,))
    invite = cursor.fetchone()
    if not invite:
        db.close()
        raise HTTPException(status_code=401, detail={"message": "Invalid invite_id"})
    email, used = invite
    if used:
        db.close()
        raise HTTPException(status_code=401, detail={"message": "Invalid invite_id"})
    cursor.execute("SELECT user_name FROM users WHERE user_name = ?", (user_name,))
    if cursor.fetchone():
        db.close()
        raise HTTPException(status_code=400, detail={"message": "Username already exists. Try providing different username."})
    hashed_password = pwd_context.hash(password)
    cursor.execute("INSERT INTO users (user_name, password) VALUES (?, ?)", (user_name, hashed_password))
    cursor.execute("UPDATE invites SET used = 1 WHERE invite_id = ?", (invite_id,))
    db.commit()
    db.close()
    return {"message": "User created"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)