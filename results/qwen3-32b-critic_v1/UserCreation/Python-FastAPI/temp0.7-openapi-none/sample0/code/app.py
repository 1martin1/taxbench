import sqlite3
import uuid
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr, field_validator
from passlib.context import CryptContext

app = FastAPI()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def init_db():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS invites
                 (email TEXT PRIMARY KEY, invite_id TEXT, created_at DATETIME)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (username TEXT PRIMARY KEY, password TEXT)''')
    conn.commit()
    conn.close()

@app.on_event("startup")
def on_startup():
    init_db()

class InviteUserRequest(BaseModel):
    email: EmailStr

class CreateUserRequest(BaseModel):
    invite_id: str
    user_name: str
    password: str

    @field_validator('user_name')
    def valid_username(cls, v: str) -> str:
        if len(v) < 3 or len(v) > 32:
            raise ValueError('Username must be between 3 and 32 characters')
        return v

    @field_validator('password')
    def valid_password(cls, v: str) -> str:
        if len(v) < 8 or len(v) > 64:
            raise ValueError('Password must be between 8 and 64 characters')
        return v

@app.post("/invite_user")
async def invite_user(request: InviteUserRequest):
    email = request.email
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    try:
        c.execute("SELECT invite_id FROM invites WHERE email = ?", (email,))
        result = c.fetchone()
        if result:
            invite_id = result[0]
            return {"invite_id": invite_id, "message": "Invitation retrieved"}
        else:
            invite_id = str(uuid.uuid4())
            c.execute("INSERT INTO invites (email, invite_id, created_at) VALUES (?, ?, datetime('now'))", 
                     (email, invite_id))
            c.execute("DELETE FROM invites WHERE created_at < datetime('now', '-24 hours')")
            conn.commit()
            return {"invite_id": invite_id, "message": "Invitation created"}
    finally:
        conn.close()

@app.post("/create_user")
async def create_user(request: CreateUserRequest):
    invite_id = request.invite_id
    user_name = request.user_name
    password = request.password

    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    try:
        c.execute("SELECT email FROM invites WHERE invite_id = ?", (invite_id,))
        result = c.fetchone()
        if not result:
            raise HTTPException(status_code=401, detail={"message": "Invalid invite_id"})
        
        c.execute("SELECT username FROM users WHERE username = ?", (user_name,))
        if c.fetchone():
            raise HTTPException(status_code=400, detail={"message": "Username already exists. Try providing different username."})
        
        hashed_password = pwd_context.hash(password)
        c.execute("INSERT INTO users (username, password) VALUES (?, ?)", (user_name, hashed_password))
        c.execute("DELETE FROM invites WHERE invite_id = ?", (invite_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail={"message": "Internal server error"}) from e
    finally:
        conn.close()
    return {"message": "User created"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)