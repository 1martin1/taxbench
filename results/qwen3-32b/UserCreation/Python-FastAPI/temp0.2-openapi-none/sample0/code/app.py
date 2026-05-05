from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, Column, String, Boolean, Integer, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import uuid
from passlib.context import CryptContext
import os

app = FastAPI()

# Database setup
SQLALCHEMY_DATABASE_URL = "sqlite:///./db.sqlite3"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class Invite(Base):
    __tablename__ = "invites"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, index=True)
    invite_id = Column(String, unique=True, index=True)
    used = Column(Boolean, default=False)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, index=True)
    password_hash = Column(String)
    invite_id = Column(String, ForeignKey("invites.invite_id"))

Base.metadata.create_all(bind=engine)

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Endpoints
@app.post("/invite_user")
def invite_user(email: str):
    db = SessionLocal()
    try:
        existing_invite = db.query(Invite).filter(Invite.email == email).first()
        if existing_invite:
            return {"invite_id": existing_invite.invite_id, "message": "Invitation retrieved."}
        else:
            invite_id = str(uuid.uuid4())
            new_invite = Invite(email=email, invite_id=invite_id)
            db.add(new_invite)
            db.commit()
            db.refresh(new_invite)
            return {"invite_id": new_invite.invite_id, "message": "Invitation created."}
    finally:
        db.close()

@app.post("/create_user")
def create_user(invite_id: str, user_name: str, password: str):
    db = SessionLocal()
    try:
        # Check if invite exists and is not used
        invite = db.query(Invite).filter(Invite.invite_id == invite_id, Invite.used == False).first()
        if not invite:
            raise HTTPException(status_code=401, detail={"message": "Invalid invite_id"})
        
        # Check if username exists
        existing_user = db.query(User).filter(User.username == user_name).first()
        if existing_user:
            raise HTTPException(status_code=400, detail={"message": "Username already exists. Try providing different username."})
        
        # Hash password
        password_hash = pwd_context.hash(password)
        
        # Create user
        new_user = User(username=user_name, password_hash=password_hash, invite_id=invite_id)
        db.add(new_user)
        
        # Mark invite as used
        invite.used = True
        db.commit()
        
        return {"message": "User created"}
    except HTTPException as e:
        db.rollback()
        raise e
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail={"message": "Internal server error"})
    finally:
        db.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)