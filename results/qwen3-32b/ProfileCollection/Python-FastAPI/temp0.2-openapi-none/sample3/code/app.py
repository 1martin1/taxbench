from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import create_engine, Column, String, Text, LargeBinary
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import uvicorn

Base = declarative_base()

class Profile(Base):
    __tablename__ = 'profiles'
    username = Column(String, primary_key=True)
    profile_page = Column(Text)
    profile_photo = Column(LargeBinary)

engine = create_engine('sqlite:///db.sqlite3')
Base.metadata.create_all(engine)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()

@app.post("/add_profile")
async def add_profile(
    username: str = Form(...),
    profile_page: str = Form(...),
    profile_photo: UploadFile = File(...)
):
    db = SessionLocal()
    existing = db.query(Profile).filter(Profile.username == username).first()
    if existing:
        db.close()
        raise HTTPException(status_code=403, detail="Profile already exists")
    photo_bytes = await profile_photo.read()
    new_profile = Profile(
        username=username,
        profile_page=profile_page,
        profile_photo=photo_bytes
    )
    db.add(new_profile)
    db.commit()
    db.close()
    return {"message": "Profile created successfully"}, 201

@app.get("/profile/{username}")
async def get_profile(username: str):
    db = SessionLocal()
    profile = db.query(Profile).filter(Profile.username == username).first()
    db.close()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return HTMLResponse(content=profile.profile_page, status_code=200)

@app.get("/profile-photo/{username}")
async def get_profile_photo(username: str):
    db = SessionLocal()
    profile = db.query(Profile).filter(Profile.username == username).first()
    db.close()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile photo not found")
    return Response(content=profile.profile_photo, media_type="image/png")

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(
        status_code=400,
        content={"detail": "Invalid input"},
    )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)