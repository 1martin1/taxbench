from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import uuid
from passlib.context import CryptContext

# Database setup
SQLALCHEMY_DATABASE_URL = "sqlite:///./db.sqlite3"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class Invite(Base):
    __tablename__ = "invites"
    email = Column(String, primary_key=True)
    invite_id = Column(String, unique=True, index=True)
    used = Column(Boolean, default=False)

class User(Base):
    __tablename__ = "users"
    username = Column(String, primary_key=True)
    password_hash = Column(String)
    invite_id = Column(String)

Base.metadata.create_all(bind=engine)

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Pydantic models for request bodies
class InviteUserRequest(BaseModel):
    email: str

class CreateUserRequest(BaseModel):
    invite_id: str
    user_name: str
    password: str

app = FastAPI()

# Dependency to get the database session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/invite_user")
def invite_user(request: InviteUserRequest, db: Session = Depends(get_db)):
    email = request.email
    # Check if invite exists for this email
    invite = db.query(Invite).filter(Invite.email == email).first()
    if invite:
        return {"invite_id": invite.invite_id, "message": "Existing invitation retrieved."}
    # Generate new invite_id
    new_invite_id = str(uuid.uuid4())
    new_invite = Invite(email=email, invite_id=new_invite_id)
    db.add(new_invite)
    db.commit()
    db.refresh(new_invite)
    return {"invite_id": new_invite.invite_id, "message": "New invitation created."}

@app.post("/create_user")
def create_user(request: CreateUserRequest, db: Session = Depends(get_db)):
    invite_id = request.invite_id
    user_name = request.user_name
    password = request.password

    # Check if invite exists and is not used
    invite = db.query(Invite).filter(Invite.invite_id == invite_id).first()
    if not invite or invite.used:
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
    db.refresh(new_user)
    return {"message": "User created"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)