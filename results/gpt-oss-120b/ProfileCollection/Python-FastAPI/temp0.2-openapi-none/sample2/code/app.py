import os
import sqlite3
from fastapi import FastAPI, Form, File, UploadFile, HTTPException, Response, status
from fastapi.responses import JSONResponse
from starlette.requests import Request

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
def on_startup():
    init_db()
    # Load APP_SECRET if needed (not used in this simple example)
    _ = os.getenv("APP_SECRET")

@app.on_event("shutdown")
def on_shutdown():
    # No persistent connections to close in this simple example
    pass

@app.post("/add_profile", status_code=status.HTTP_201_CREATED, responses={
    201: {"description": "Profile created successfully"},
    403: {"description": "Profile already exists, creation forbidden"},
    400: {"description": "Invalid input"},
})
async def add_profile(
    username: str = Form(...),
    profile_page: str = Form(...),
    profile_photo: UploadFile = File(...)
):
    # Basic validation
    if not username.strip():
        raise HTTPException(status_code=400, detail="Username cannot be empty")
    if profile_photo.content_type != "image/png":
        raise HTTPException(status_code=400, detail="Profile photo must be a PNG image")
    photo_bytes = await profile_photo.read()
    if not photo_bytes:
        raise HTTPException(status_code=400, detail="Empty profile photo")

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT 1 FROM profiles WHERE username = ?", (username,))
        if cursor.fetchone():
            raise HTTPException(status_code=403, detail="Profile already exists")
        cursor.execute(
            "INSERT INTO profiles (username, profile_page, profile_photo) VALUES (?, ?, ?)",
            (username, profile_page, photo_bytes)
        )
        conn.commit()
    finally:
        conn.close()
    return Response(status_code=201)

@app.get("/profile/{username}", responses={
    200: {"description": "Profile retrieved successfully", "content": {"text/html": {}}},
    404: {"description": "Profile not found"},
})
def get_profile(username: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT profile_page FROM profiles WHERE username = ?",
        (username,)
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Profile not found")
    html_content = row["profile_page"]
    return Response(content=html_content, media_type="text/html")

@app.get("/profile-photo/{username}", responses={
    200: {"description": "Profile photo retrieved successfully", "content": {"image/png": {}}},
    404: {"description": "Profile photo not found"},
})
def get_profile_photo(username: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT profile_photo FROM profiles WHERE username = ?",
        (username,)
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Profile photo not found")
    photo_bytes = row["profile_photo"]
    return Response(content=photo_bytes, media_type="image/png")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)