import sqlite3
import uuid
import time
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr, StringConstraints
from typing_extensions import Annotated
from passlib.context import CryptContext
import os

app = FastAPI()

DATABASE_URL = "db.sqlite3"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

@app.on_event("startup")
def create_tables():
    with sqlite3.connect(DATABASE_URL) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS invites (
                email TEXT PRIMARY KEY,
                invite_id TEXT NOT NULL,
                used BOOLEAN NOT NULL DEFAULT 0
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                invite_id TEXT,
                FOREIGN KEY(invite_id) REFERENCES invites(invite_id)
            )
        """)
        conn.commit()

class InviteUserRequest(BaseModel):
    email: EmailStr

class InviteUserResponse(BaseModel):
    invite_id: str
    message: str

class CreateUserRequest(BaseModel):
    invite_id: str
    user_name: Annotated[str, StringConstraints(min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_-]+$")]
    password: str

class CreateUserResponse(BaseModel):
    message: str

@app.post("/invite_user", response_model=InviteUserResponse)
async def invite_user(request: InviteUserRequest):
    email = request.email
    max_retries = 5
    retry_delay = 0.1
    for attempt in range(max_retries):
        try:
            with sqlite3.connect(DATABASE_URL) as conn:
                conn.execute("PRAGMA busy_timeout = 500")
                cursor = conn.cursor()
                cursor.execute("BEGIN IMMEDIATE")
                cursor.execute("SELECT invite_id, used FROM invites WHERE email = ?", (email,))
                result = cursor.fetchone()
                if result:
                    invite_id, used = result
                    return {"invite_id": invite_id, "message": "Existing invitation retrieved."}
                new_invite_id = str(uuid.uuid4())
                cursor.execute("INSERT INTO invites (email, invite_id, used) VALUES (?, ?, ?)", (email, new_invite_id, 0))
                conn.commit()
            return {"invite_id": new_invite_id, "message": "New invitation created."}
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e) and attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                raise
        except Exception as e:
            raise

@app.post("/create_user", response_model=CreateUserResponse)
async def create_user(request: CreateUserRequest):
    invite_id = request.invite_id
    username = request.user_name
    password = request.password
    max_retries = 5
    retry_delay = 0.1
    for attempt in range(max_retries):
        try:
            with sqlite3.connect(DATABASE_URL) as conn:
                conn.execute("PRAGMA busy_timeout = 500")
                cursor = conn.cursor()
                cursor.execute("BEGIN IMMEDIATE")
                cursor.execute("SELECT email FROM invites WHERE invite_id = ? AND used = 0", (invite_id,))
                if not cursor.fetchone():
                    raise HTTPException(status_code=401, detail={"message": "Invalid invite_id"})
                cursor.execute("SELECT username FROM users WHERE username = ?", (username,))
                if cursor.fetchone():
                    raise HTTPException(status_code=400, detail={"message": "Username already exists. Try providing different username."})
                password_hash = hash_password(password)
                cursor.execute("INSERT INTO users (username, password_hash, invite_id) VALUES (?, ?, ?)", (username, password_hash, invite_id))
                cursor.execute("UPDATE invites SET used = 1 WHERE invite_id = ?", (invite_id,))
                conn.commit()
            return {"message": "User created"}
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e) and attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                raise
        except Exception as e:
            raise

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)