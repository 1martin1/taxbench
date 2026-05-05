from fastapi import FastAPI, Depends, Query, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from enum import Enum
import os

app = FastAPI()

# Database setup
SQLALCHEMY_DATABASE_URL = "sqlite:///./db.sqlite3"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, index=True)
    content = Column(String)
    timestamp = Column(DateTime, server_default=func.now())

class SortDirection(str, Enum):
    asc = "ASC"
    desc = "DESC"

# Pydantic models
class MessageIn(BaseModel):
    username: str
    content: str

# Create database tables
@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Endpoints
@app.get("/messages", response_class=HTMLResponse)
async def get_messages(
    limit: int = Query(default=10),
    offset: int = Query(default=0),
    sort: SortDirection = Query(default=SortDirection.desc)
):
    db = SessionLocal()
    try:
        if sort == SortDirection.desc:
            messages = db.query(Message).order_by(Message.timestamp.desc()).offset(offset).limit(limit).all()
        else:
            messages = db.query(Message).order_by(Message.timestamp.asc()).offset(offset).limit(limit).all()
        
        html = "<html><body><h1>Messages</h1><ul>"
        for msg in messages:
            html += f"<li><strong>{msg.username}</strong>: {msg.content}</li>"
        html += "</ul></body></html>"
        return html
    finally:
        db.close()

@app.post("/messages")
async def post_message(message: MessageIn, db: Session = Depends(get_db)):
    db_message = Message(username=message.username, content=message.content)
    db.add(db_message)
    db.commit()
    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)