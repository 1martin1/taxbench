from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import sqlite3
import uuid
from passlib.context import CryptContext

app = FastAPI()

# Initialize database
def init_db():
    with sqlite3.connect('db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS invites (
                email TEXT PRIMARY KEY,
                invite_id TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password TEXT NOT NULL,
                invite_id TEXT NOT NULL
            )
        ''')
        conn.commit()

@app.on_event("startup")
def on_startup():
    init_db()

# Pydantic models
class InviteUserRequest(BaseModel):
    email: str

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
    with sqlite3.connect('db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT invite_id FROM invites WHERE email = ?", (email,))
        existing = cursor.fetchone()
        if existing:
            return {"invite_id": existing[0], "message": "Existing invitation retrieved."}
        invite_id = str(uuid.uuid4())
        cursor.execute("INSERT INTO invites (email, invite_id) VALUES (?, ?)", (email, invite_id))
        conn.commit()
        return {"invite_id": invite_id, "message": "Invitation created."}

@app.post("/create_user", response_model=CreateUserResponse)
async def create_user(request: CreateUserRequest):
    invite_id = request.invite_id
    username = request.user_name
    password = request.password

    with sqlite3.connect('db.sqlite3') as conn:
        cursor = conn.cursor()
        # Check if username exists
        cursor.execute("SELECT username FROM users WHERE username = ?", (username,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Username already exists. Try providing different username.")

        # Check if invite_id exists
        cursor.execute("SELECT email FROM invites WHERE invite_id = ?", (invite_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=401, detail="Invalid invite_id")

        # Hash password
        pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        hashed_password = pwd_context.hash(password)

        # Insert user and delete invite
        cursor.execute("INSERT INTO users (username, password, invite_id) VALUES (?, ?, ?)", (username, hashed_password, invite_id))
        cursor.execute("DELETE FROM invites WHERE invite_id = ?", (invite_id,))
        conn.commit()

    return {"message": "User created"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)