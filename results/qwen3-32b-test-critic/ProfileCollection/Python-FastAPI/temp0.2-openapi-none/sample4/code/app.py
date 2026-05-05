from fastapi import FastAPI, Form, File, UploadFile, HTTPException, Response
from fastapi.responses import HTMLResponse
import sqlite3
import os

app = FastAPI()

DB_NAME = 'db.sqlite3'

def get_db():
    db = sqlite3.connect(DB_NAME)
    db.row_factory = sqlite3.Row
    return db

@app.on_event("startup")
def create_tables():
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            username TEXT PRIMARY KEY,
            profile_page TEXT NOT NULL,
            profile_photo BLOB NOT NULL
        )
    """)
    db.commit()
    db.close()

@app.post("/add_profile")
async def add_profile(
    username: str = Form(...),
    profile_page: str = Form(...),
    profile_photo: UploadFile = File(...)
):
    db = get_db()
    existing = db.execute("SELECT 1 FROM profiles WHERE username = ?", (username,)).fetchone()
    if existing:
        db.close()
        raise HTTPException(status_code=403, detail="Profile already exists")
    
    photo_contents = await profile_photo.read()
    try:
        db.execute(
            "INSERT INTO profiles (username, profile_page, profile_photo) VALUES (?, ?, ?)",
            (username, profile_page, photo_contents)
        )
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        db.close()
    
    return {"status": "Profile created successfully"}

@app.get("/profile/{username}")
async def get_profile(username: str):
    db = get_db()
    profile = db.execute("SELECT profile_page FROM profiles WHERE username = ?", (username,)).fetchone()
    db.close()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return HTMLResponse(content=profile['profile_page'], status_code=200)

@app.get("/profile-photo/{username}")
async def get_profile_photo(username: str):
    db = get_db()
    profile = db.execute("SELECT profile_photo FROM profiles WHERE username = ?", (username,)).fetchone()
    db.close()
    if not profile or profile['profile_photo'] is None:
        raise HTTPException(status_code=404, detail="Profile photo not found")
    return Response(content=profile['profile_photo'], media_type="image/png")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)