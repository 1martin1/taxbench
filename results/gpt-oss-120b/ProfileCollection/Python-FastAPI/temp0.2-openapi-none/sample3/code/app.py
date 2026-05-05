import os
import sqlite3
from fastapi import FastAPI, Form, File, UploadFile, HTTPException, Response
from fastapi.responses import JSONResponse
from starlette.status import HTTP_201_CREATED, HTTP_400_BAD_REQUEST, HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND
import uvicorn

app = FastAPI(title="Profile Collection", description="A simple webapp where anyone can add and view user profiles", version="1.0.0")

DB_PATH = "db.sqlite3"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            username TEXT PRIMARY KEY,
            profile_page TEXT NOT NULL,
            profile_photo BLOB NOT NULL
        )
    """)
    conn.commit()
    conn.close()

@app.on_event("startup")
def startup_event():
    init_db()

@app.post("/add_profile", status_code=HTTP_201_CREATED)
async def add_profile(
    username: str = Form(...),
    profile_page: str = Form(...),
    profile_photo: UploadFile = File(...)
):
    # Basic validation
    if not username.strip():
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Username cannot be empty")
    if profile_photo.content_type != "image/png":
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Profile photo must be a PNG image")
    try:
        photo_bytes = await profile_photo.read()
    except Exception:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Could not read uploaded photo")
    conn = get_db_connection()
    cursor = conn.cursor()
    # Check if username already exists
    cursor.execute("SELECT 1 FROM profiles WHERE username = ?", (username,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="Profile already exists")
    # Insert new profile
    try:
        cursor.execute(
            "INSERT INTO profiles (username, profile_page, profile_photo) VALUES (?, ?, ?)",
            (username, profile_page, photo_bytes)
        )
        conn.commit()
    except sqlite3.Error as e:
        conn.close()
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Database error")
    conn.close()
    return JSONResponse(status_code=HTTP_201_CREATED, content={"message": "Profile created successfully"})

@app.get("/profile/{username}")
def get_profile(username: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT profile_page FROM profiles WHERE username = ?", (username,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Profile not found")
    html_content = row["profile_page"]
    return Response(content=html_content, media_type="text/html")

@app.get("/profile-photo/{username}")
def get_profile_photo(username: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT profile_photo FROM profiles WHERE username = ?", (username,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Profile photo not found")
    photo_bytes = row["profile_photo"]
    return Response(content=photo_bytes, media_type="image/png")

if __name__ == "__main__":
    # Optional: load secret from environment (not used in this simple app)
    _ = os.getenv("APP_SECRET")
    uvicorn.run(app, host="0.0.0.0", port=5000)