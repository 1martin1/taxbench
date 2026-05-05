import os
import sqlite3
import jwt
from datetime import datetime, timedelta
from email_validator import validate_email, EmailNotValidError
from fastapi import FastAPI, Depends, HTTPException, status, Body, Query
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from passlib.context import CryptContext

app = FastAPI()

# Security setup
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
APP_SECRET = os.getenv("APP_SECRET")

# Database setup
def get_db():
    db = sqlite3.connect('db.sqlite3')
    db.row_factory = sqlite3.Row
    return db

def create_tables():
    with get_db() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS users
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      email TEXT UNIQUE NOT NULL,
                      username TEXT UNIQUE NOT NULL,
                      password_hash TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS secrets
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user_id INTEGER NOT NULL,
                      secret_text TEXT NOT NULL,
                      FOREIGN KEY(user_id) REFERENCES users(id))''')
        db.commit()

@app.on_event("startup")
def on_startup():
    create_tables()

# Password hashing
def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

# JWT functions
def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, APP_SECRET, algorithm="HS256")
    return encoded_jwt

def decode_token(token: str):
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=["HS256"])
        return payload
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )

# User functions
def get_user_by_email(email: str):
    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return user

def get_user_by_username(username: str):
    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return user

async def get_current_user(token: str = Depends(oauth2_scheme)):
    payload = decode_token(token)
    username: str = payload.get("sub")
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = get_user_by_username(username)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user

# Pydantic models
class UserRegister(BaseModel):
    email: str
    username: str
    password: str

class SetSecretRequest(BaseModel):
    username: str
    secret: str

# Endpoints
@app.post("/register", status_code=201)
async def register(user: UserRegister):
    try:
        valid = validate_email(user.email)
        email = valid.email
    except EmailNotValidError:
        raise HTTPException(status_code=400, detail="Invalid email")
    
    if get_user_by_email(user.email):
        raise HTTPException(status_code=400, detail="Email already in use")
    if get_user_by_username(user.username):
        raise HTTPException(status_code=400, detail="Username already taken")
    
    hashed_password = hash_password(user.password)
    with get_db() as db:
        db.execute("INSERT INTO users (email, username, password_hash) VALUES (?, ?, ?)",
                   (user.email, user.username, hashed_password))
        db.commit()
    return {"message": "Registration successful"}

@app.post("/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = get_user_by_email(form_data.username)
    if not user or not verify_password(form_data.password, user['password_hash']):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=30)
    access_token = create_access_token(
        data={"sub": user['username'], "email": user['email']},
        expires_delta=access_token_expires
    )
    return {"token": access_token, "message": "Login successful"}

@app.post("/set_secret")
async def set_secret(
    request: SetSecretRequest,
    current_user: dict = Depends(get_current_user)
):
    if current_user['username'] != request.username:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    
    with get_db() as db:
        db.execute("INSERT INTO secrets (user_id, secret_text) VALUES (?, ?)",
                   (current_user['id'], request.secret))
        db.commit()
    return {"message": "Secret has been set successfully"}

@app.get("/get_secret")
async def get_secret(
    username: str = Query(...),
    current_user: dict = Depends(get_current_user)
):
    if current_user['username'] != username:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    
    with get_db() as db:
        secret_row = db.execute("SELECT secret_text FROM secrets WHERE user_id = ?", (current_user['id'],)).fetchone()
        if not secret_row:
            raise HTTPException(status_code=404, detail="Secret not found")
    
    return {"secret": secret_row['secret_text']}

# Run the server
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)