from fastapi import FastAPI, Depends, HTTPException, Request, status
from sqlalchemy import create_engine, Column, Integer, String, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from pydantic import BaseModel, Field
import uuid
from passlib.context import CryptContext
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
from typing import Dict, List

load_dotenv()

SQLALCHEMY_DATABASE_URL = "sqlite:///./db.sqlite3"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Invitation(Base):
    __tablename__ = 'invitations'
    id = Column(Integer, primary_key=True)
    email = Column(String(254), unique=True, index=True)
    invite_id = Column(String(36), unique=True)
    used = Column(Boolean, default=False)

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    user_name = Column(String(50), unique=True, index=True)
    password_hash = Column(String)
    invite_id = Column(String(36), ForeignKey('invitations.invite_id'))

Base.metadata.create_all(bind=engine)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

app = FastAPI()

rate_limits: Dict[str, List[datetime]] = {}
RATE_LIMIT = 10  # Max requests per minute
RATE_WINDOW = timedelta(minutes=1)

def rate_limit_dependency(request: Request):
    client_host = request.client.host
    now = datetime.now()
    if client_host not in rate_limits:
        rate_limits[client_host] = []
    # Filter timestamps within the current window
    rate_limits[client_host] = [ts for ts in rate_limits[client_host] if ts > now - RATE_WINDOW]
    if len(rate_limits[client_host]) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many requests. Please try again later.")
    rate_limits[client_host].append(now)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class InviteUserRequest(BaseModel):
    email: str = Field(..., max_length=254)

class CreateUserRequest(BaseModel):
    invite_id: str = Field(..., max_length=36)
    user_name: str = Field(..., max_length=50)
    password: str = Field(..., max_length=100)

@app.post("/invite_user")
async def invite_user(
    request: InviteUserRequest, 
    db: Session = Depends(get_db),
    rate_limit: None = Depends(rate_limit_dependency)
):
    email = request.email
    existing_invitation = db.query(Invitation).filter(Invitation.email == email).first()
    if existing_invitation:
        return {"invite_id": existing_invitation.invite_id, "message": "Existing invitation"}
    invite_id = str(uuid.uuid4())
    new_invitation = Invitation(email=email, invite_id=invite_id)
    db.add(new_invitation)
    db.commit()
    db.refresh(new_invitation)
    return {"invite_id": new_invitation.invite_id, "message": "Invitation created"}

@app.post("/create_user")
async def create_user(request: CreateUserRequest, db: Session = Depends(get_db)):
    user_name = request.user_name
    existing_user = db.query(User).filter(User.user_name == user_name).first()
    if existing_user:
        raise HTTPException(status_code=400, detail={"message": "Username already exists. Try providing different username."})
    
    invitation = db.query(Invitation).filter(Invitation.invite_id == request.invite_id).first()
    if not invitation or invitation.used:
        raise HTTPException(status_code=401, detail={"message": "Invalid invite_id"})
    
    password_hash = pwd_context.hash(request.password)
    new_user = User(user_name=user_name, password_hash=password_hash, invite_id=request.invite_id)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    invitation.used = True
    db.commit()
    
    return {"message": "User created"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)