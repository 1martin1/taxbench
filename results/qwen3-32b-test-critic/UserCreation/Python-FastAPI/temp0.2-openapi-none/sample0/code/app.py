from fastapi import FastAPI, HTTPException
import sqlite3
import uuid
from passlib.context import CryptContext
from email_validator import validate_email, EmailNotValidError

app = FastAPI()

# Database initialization
def init_db():
    conn = sqlite3.connect('db.sqlite3', check_same_thread=False)
    with conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS invites
                        (id INTEGER PRIMARY KEY AUTOINCREMENT,
                         email TEXT UNIQUE NOT NULL,
                         invite_id TEXT UNIQUE NOT NULL,
                         used BOOLEAN DEFAULT 0)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS users
                        (id INTEGER PRIMARY KEY AUTOINCREMENT,
                         username TEXT UNIQUE NOT NULL,
                         password_hash TEXT NOT NULL,
                         invite_id TEXT NOT NULL,
                         FOREIGN KEY(invite_id) REFERENCES invites(invite_id))''')
    conn.close()

init_db()

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Database connection helper
def get_db():
    return sqlite3.connect('db.sqlite3', check_same_thread=False)

@app.post("/invite_user")
def invite_user(email: str):
    try:
        # Validate email format
        valid = validate_email(email)
        email = valid.email  # normalized form
    except EmailNotValidError:
        raise HTTPException(status_code=400, detail={"message": "Invalid email address"})

    conn = get_db()
    try:
        cursor = conn.cursor()
        # Check if email already has an invite
        cursor.execute("SELECT invite_id, used FROM invites WHERE email = ?", (email,))
        existing_invite = cursor.fetchone()
        
        if existing_invite:
            invite_id, used = existing_invite
            return {"invite_id": invite_id, "message": "Invitation retrieved."}
        
        # Create new invitation
        invite_id = str(uuid.uuid4())
        cursor.execute("INSERT INTO invites (email, invite_id, used) VALUES (?, ?, ?)", 
                      (email, invite_id, False))
        conn.commit()
        return {"invite_id": invite_id, "message": "Invitation created."}
    except sqlite3.IntegrityError:
        # Handle potential race condition
        conn.rollback()
        cursor.execute("SELECT invite_id FROM invites WHERE email = ?", (email,))
        existing_invite = cursor.fetchone()
        if existing_invite:
            return {"invite_id": existing_invite[0], "message": "Invitation retrieved."}
        raise HTTPException(status_code=500, detail={"message": "Database error"})
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=500, detail={"message": "Internal server error"})
    finally:
        conn.close()

@app.post("/create_user")
def create_user(invite_id: str, user_name: str, password: str):
    conn = get_db()
    try:
        cursor = conn.cursor()
        # Check if invite exists and is not used
        cursor.execute("SELECT id FROM invites WHERE invite_id = ? AND used = 0", (invite_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=401, detail={"message": "Invalid invite_id"})
        
        # Check if username exists
        cursor.execute("SELECT id FROM users WHERE username = ?", (user_name,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail={"message": "Username already exists. Try providing different username."})
        
        # Hash password
        password_hash = pwd_context.hash(password)
        
        # Create user and mark invite as used in transaction
        cursor.execute("BEGIN")
        try:
            cursor.execute("INSERT INTO users (username, password_hash, invite_id) VALUES (?, ?, ?)", 
                          (user_name, password_hash, invite_id))
            cursor.execute("UPDATE invites SET used = 1 WHERE invite_id = ?", (invite_id,))
            conn.commit()
        except sqlite3.IntegrityError as e:
            conn.rollback()
            if "UNIQUE constraint failed: users.username" in str(e):
                raise HTTPException(status_code=400, detail={"message": "Username already exists. Try providing different username."})
            raise HTTPException(status_code=500, detail={"message": "Database error"})
        return {"message": "User created"}
    except Exception as e:
        conn.rollback()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail={"message": "Internal server error"})
    finally:
        conn.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)