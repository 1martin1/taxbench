from fastapi import FastAPI, Depends, HTTPException, Request
from sqlalchemy import create_engine, Column, String, Boolean
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
import uuid
from pydantic import BaseModel, EmailStr, Field
from passlib.hash import bcrypt
import os
import jwt
from datetime import datetime, timedelta
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from time import time

app = FastAPI()

# Database setup
SQLALCHEMY_DATABASE_URL = "sqlite:///./db.sqlite3"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class Invitation(Base):
    __tablename__ = "invitations"
    email = Column(String, primary_key=True, index=True)
    invite_id = Column(String, unique=True, index=True)
    used = Column(Boolean, default=False)

class User(Base):
    __tablename__ = "users"
    username = Column(String, primary_key=True, index=True)
    password_hash = Column(String)

# Create database tables
@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)

# Request/Response Models
class InviteUserRequest(BaseModel):
    email: EmailStr

class InviteUserResponse(BaseModel):
    invite_id: str
    message: str

class CreateUserRequest(BaseModel):
    invite_id: str
    user_name: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=100)

class CreateUserResponse(BaseModel):
    message: str

# Rate limiting
RATE_LIMITS = {}

def rate_limit(key, limit=5, window=60):
    def decorator(func):
        async def wrapper(*args, **kwargs):
            now = time()
            if key in RATE_LIMITS:
                RATE_LIMITS[key] = [t for t in RATE_LIMITS[key] if t > now - window]
            if len(RATE_LIMITS.get(key, [])) >= limit:
                raise HTTPException(status_code=429, detail="Too many requests, please try again later")
            RATE_LIMITS.setdefault(key, []).append(now)
            return await func(*args, **kwargs)
        return wrapper
    return decorator

# Authentication
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=401,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, os.getenv("APP_SECRET"), algorithms=["HS256"])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception
    return username

# Endpoints
@app.post("/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    db = SessionLocal()
    user = db.query(User).filter(User.username == form_data.username).first()
    if not user or not bcrypt.verify(form_data.password, user.password_hash):
        raise HTTPException(status_code=400, detail="Incorrect username or password")
    access_token_expires = timedelta(minutes=30)
    access_token = jwt.encode(
        {"sub": user.username, "exp": datetime.utcnow() + access_token_expires},
        os.getenv("APP_SECRET")
    )
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/invite_user", response_model=InviteUserResponse)
async def invite_user(
    request: InviteUserRequest,
    current_user: str = Depends(get_current_user),
    request_obj: Request = None
):
    # Rate limit per user
    key = current_user
    now = time()
    if key in RATE_LIMITS:
        RATE_LIMITS[key] = [t for t in RATE_LIMITS[key] if t > now - 60]
    if len(RATE_LIMITS.get(key, [])) >= 5:
        raise HTTPException(status_code=429, detail="Too many requests, please try again later")
    RATE_LIMITS.setdefault(key, []).append(now)
    
    db = SessionLocal()
    email = request.email
    invitation = db.query(Invitation).filter(Invitation.email == email).first()
    
    if not invitation:
        invite_id = str(uuid.uuid4())
        invitation = Invitation(email=email, invite_id=invite_id)
        db.add(invitation)
        db.commit()
        db.refresh(invitation)
        message = "Invitation created."
    else:
        message = "Invitation retrieved."
    
    return {"invite_id": invitation.invite_id, "message": message}

@app.post("/create_user")
async def create_user(
    request: CreateUserRequest,
    request_obj: Request = None
):
    # Rate limit per IP
    ip = request_obj.client.host
    now = time()
    if ip in RATE_LIMITS:
        RATE_LIMITS[ip] = [t for t in RATE_LIMITS[ip] if t > now - 60]
    if len(RATE_LIMITS.get(ip, [])) >= 3:
        raise HTTPException(status_code=429, detail="Too many requests, please try again later")
    RATE_LIMITS.setdefault(ip, []).append(now)
    
    db = SessionLocal()
    invite_id = request.invite_id
    username = request.user_name
    password = request.password
    
    # Validate invite_id
    invitation = db.query(Invitation).filter(Invitation.invite_id == invite_id).first()
    if not invitation or invitation.used:
        raise HTTPException(status_code=401, detail={"message": "Invalid invite_id"})
    
    # Check if username exists
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=400, detail={"message": "Username already exists. Try providing different username."})
    
    # Create user
    password_hash = bcrypt.hash(password)
    db_user = User(username=username, password_hash=password_hash)
    db.add(db_user)
    invitation.used = True
    db.commit()
    
    return {"message": "User created"}

# Run the server
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)