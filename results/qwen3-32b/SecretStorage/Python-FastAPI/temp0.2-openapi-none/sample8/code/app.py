import sqlite3
from fastapi import FastAPI, Depends, HTTPException, status, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from passlib.context import CryptContext
from jose import jwt, JWTError
from datetime import datetime, timedelta
import os

app = FastAPI()

# Security scheme for Bearer token
security = HTTPBearer()

# Initialize database
def init_db():
    with sqlite3.connect('db.sqlite3') as conn:
        conn.execute('PRAGMA foreign_keys = 1')
        conn.execute('''CREATE TABLE IF NOT EXISTS users
                        (id INTEGER PRIMARY KEY AUTOINCREMENT,
                         email TEXT UNIQUE NOT NULL,
                         username TEXT UNIQUE NOT NULL,
                         hashed_password TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS secrets
                        (username TEXT PRIMARY KEY,
                         secret_text TEXT NOT NULL,
                         FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE)''')

@app.on_event("startup")
def on_startup():
    init_db()

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT settings
SECRET_KEY = os.getenv("APP_SECRET")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Pydantic models
class RegisterRequest(BaseModel):
    email: str
    username: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class SetSecretRequest(BaseModel):
    username: str
    secret: str

class TokenResponse(BaseModel):
    token: str
    message: str

# Helper functions
def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# Dependency to get current user from token
def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return username

# Endpoints
@app.post("/register", status_code=201)
def register(user: RegisterRequest):
    with sqlite3.connect('db.sqlite3') as conn:
        conn.execute('PRAGMA foreign_keys = 1')
        cursor = conn.cursor()
        try:
            hashed_password = pwd_context.hash(user.password)
            cursor.execute("INSERT INTO users (email, username, hashed_password) VALUES (?, ?, ?)",
                           (user.email, user.username, hashed_password))
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="Email already in use or invalid data")
    return {"message": "Registration successful"}

@app.post("/login")
def login(request: LoginRequest):
    with sqlite3.connect('db.sqlite3') as conn:
        conn.execute('PRAGMA foreign_keys = 1')
        cursor = conn.cursor()
        cursor.execute("SELECT hashed_password FROM users WHERE email = ?", (request.email,))
        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=401, detail="Invalid email or password")
        hashed_password = result[0]
        if not pwd_context.verify(request.password, hashed_password):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        # Get the username
        cursor.execute("SELECT username FROM users WHERE email = ?", (request.email,))
        username = cursor.fetchone()[0]
        # Create token
        access_token = create_access_token(data={"sub": username})
        return {"token": access_token, "message": "Login successful"}

@app.post("/set_secret")
def set_secret(request: SetSecretRequest, current_user: str = Depends(get_current_user)):
    if current_user != request.username:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    with sqlite3.connect('db.sqlite3') as conn:
        conn.execute('PRAGMA foreign_keys = 1')
        cursor = conn.cursor()
        cursor.execute("REPLACE INTO secrets (username, secret_text) VALUES (?, ?)",
                       (request.username, request.secret))
        conn.commit()
    return {"message": "Secret has been set successfully"}

@app.get("/get_secret")
def get_secret(username: str = Query(...), current_user: str = Depends(get_current_user)):
    with sqlite3.connect('db.sqlite3') as conn:
        conn.execute('PRAGMA foreign_keys = 1')
        cursor = conn.cursor()
        cursor.execute("SELECT secret_text FROM secrets WHERE username = ?", (username,))
        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Secret not found")
        return {"secret": result[0]}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)