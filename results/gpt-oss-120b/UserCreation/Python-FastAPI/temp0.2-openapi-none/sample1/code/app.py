import os
import sqlite3
import uuid
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
import uvicorn

# Initialize FastAPI app
app = FastAPI(title="InviteAPI", description="Provides invite-only micro-service.", version="1.0.11")

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Database connection (allow usage from multiple threads)
DB_PATH = "db.sqlite3"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row

def init_db():
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS invitations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                invite_id TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                email TEXT NOT NULL,
                invite_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (invite_id) REFERENCES invitations(invite_id)
            )
            """
        )

# Run DB init on startup
@app.on_event("startup")
def on_startup():
    init_db()

# Pydantic models for request bodies
class InviteUserRequest(BaseModel):
    email: EmailStr

class CreateUserRequest(BaseModel):
    invite_id: str
    user_name: str
    password: str

# Endpoint: /invite_user
@app.post("/invite_user")
def invite_user(payload: InviteUserRequest):
    email = payload.email.lower()
    cur = conn.cursor()
    cur.execute("SELECT invite_id FROM invitations WHERE email = ?", (email,))
    row = cur.fetchone()
    if row:
        invite_id = row["invite_id"]
    else:
        invite_id = uuid.uuid4().hex
        try:
            cur.execute(
                "INSERT INTO invitations (email, invite_id) VALUES (?, ?)",
                (email, invite_id),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            # In case of race condition, fetch the existing invite_id
            cur.execute("SELECT invite_id FROM invitations WHERE email = ?", (email,))
            invite_id = cur.fetchone()["invite_id"]
    return {"invite_id": invite_id, "message": "Invitation created"}

# Endpoint: /create_user
@app.post("/create_user")
def create_user(payload: CreateUserRequest):
    cur = conn.cursor()
    # Verify invite_id exists
    cur.execute("SELECT email FROM invitations WHERE invite_id = ?", (payload.invite_id,))
    invite_row = cur.fetchone()
    if not invite_row:
        raise HTTPException(status_code=401, detail="Invalid invite_id")
    email = invite_row["email"]

    # Check if username already exists
    cur.execute("SELECT id FROM users WHERE username = ?", (payload.user_name,))
    if cur.fetchone():
        raise HTTPException(
            status_code=400,
            detail="Username already exists. Try providing different username."
        )

    # Hash the password
    password_hash = pwd_context.hash(payload.password)

    # Create the user
    try:
        cur.execute(
            """
            INSERT INTO users (username, password_hash, email, invite_id)
            VALUES (?, ?, ?, ?)
            """,
            (payload.user_name, password_hash, email, payload.invite_id),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Could not create user due to database error.")

    return {"message": "User created successfully"}

if __name__ == "__main__":
    # Use APP_SECRET if needed in future extensions
    _ = os.getenv("APP_SECRET", "")
    uvicorn.run(app, host="0.0.0.0", port=5000)