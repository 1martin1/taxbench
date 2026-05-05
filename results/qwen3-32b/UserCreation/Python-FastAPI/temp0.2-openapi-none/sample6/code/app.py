from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy import create_engine, Column, String, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import uuid
from passlib.context import CryptContext
import os

# Database setup
SQLALCHEMY_DATABASE_URL = "sqlite:///./db.sqlite3"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class Invitation(Base):
    __tablename__ = "invitations"
    invite_id = Column(String, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    used = Column(Boolean, default=False)

class User(Base):
    __tablename__ = "users"
    user_name = Column(String, primary_key=True)
    password_hash = Column(String, nullable=False)

Base.metadata.create_all(bind=engine)

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# FastAPI app
app = FastAPI()

# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Routes
@app.post("/invite_user")
async def invite_user(email: str, db: Session = Depends(get_db)):
    existing = db.query(Invitation).filter(Invitation.email == email).first()
    if existing:
        return {"invite_id": existing.invite_id, "message": "Invitation retrieved."}
    invite_id = str(uuid.uuid4())
    new_invite = Invitation(invite_id=invite_id, email=email)
    db.add(new_invite)
    db.commit()
    db.refresh(new_invite)
    return {"invite_id": invite_id, "message": "Invitation created."}

@app.post("/create_user")
async def create_user(invite_id: str, user_name: str, password: str, db: Session = Depends(get_db)):
    invite = db.query(Invitation).filter(Invitation.invite_id == invite_id).first()
    if not invite or invite.used:
        raise HTTPException(status_code=401, detail={"message": "Invalid invite_id"})
    if db.query(User).filter(User.user_name == user_name).first():
        raise HTTPException(status_code=400, detail={"message": "Username already exists. Try providing different username."})
    hashed = pwd_context.hash(password)
    new_user = User(user_name=user_name, password_hash=hashed)
    db.add(new_user)
    invite.used = True
    db.commit()
    return {"message": "User created"}

# Run the app
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)