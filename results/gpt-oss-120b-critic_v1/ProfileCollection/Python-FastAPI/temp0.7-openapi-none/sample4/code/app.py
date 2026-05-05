import os
import re
import sqlite3
from typing import Generator

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Depends, Request
from fastapi.responses import HTMLResponse, Response
from starlette.status import (
    HTTP_201_CREATED,
    HTTP_400_BAD_REQUEST,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
)
import uvicorn

# -------------------- Configuration --------------------
DATABASE = "db.sqlite3"
MAX_PHOTO_SIZE = 2 * 1024 * 1024          # 2 MB
MAX_PROFILE_PAGE_SIZE = 10 * 1024         # 10 KB
USERNAME_REGEX = re.compile(r"^[A-Za-z0-9_]{1,30}$")  # Alphanumeric + underscore, max 30 chars

# -------------------- Database utilities --------------------
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """
    Dependency that provides a SQLite connection.
    check_same_thread=False allows the connection to be used in the thread that FastAPI runs.
    """
    conn = sqlite3.connect(DATABASE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db() -> None:
    with sqlite3.connect(DATABASE, check_same_thread=False) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                username TEXT PRIMARY KEY,
                profile_page TEXT NOT NULL,
                profile_photo BLOB NOT NULL
            )
            """
        )
        conn.commit()

def profile_exists(conn: sqlite3.Connection, username: str) -> bool:
    cur = conn.execute("SELECT 1 FROM profiles WHERE username = ?", (username,))
    return cur.fetchone() is not None

# -------------------- Application --------------------
app = FastAPI(
    title="Profile Collection",
    description="A simple webapp where anyone can add and view user profiles",
    version="1.0.0",
)

@app.on_event("startup")
def on_startup() -> None:
    init_db()  # Ensure the database and tables exist

# -------------------- Endpoints --------------------
@app.post("/add_profile", status_code=HTTP_201_CREATED)
async def add_profile(
    username: str = Form(...),
    profile_page: str = Form(...),
    profile_photo: UploadFile = File(...),
    db: sqlite3.Connection = Depends(get_db),
):
    # ---- Validate username ----
    if not USERNAME_REGEX.fullmatch(username):
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Invalid username. Use 1-30 alphanumeric characters or underscores.",
        )

    # ---- Validate profile_page size ----
    if len(profile_page.encode("utf-8")) > MAX_PROFILE_PAGE_SIZE:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=f"Profile page too large (max {MAX_PROFILE_PAGE_SIZE} bytes).",
        )

    # Simple XSS mitigation: reject obvious script tags
    if "<script" in profile_page.lower():
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Profile page contains disallowed content.",
        )

    # ---- Validate uploaded photo ----
    if profile_photo.content_type != "image/png":
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Profile photo must be a PNG image.",
        )

    # Read with size limit
    photo_bytes = await profile_photo.read()
    if len(photo_bytes) > MAX_PHOTO_SIZE:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=f"Profile photo too large (max {MAX_PHOTO_SIZE} bytes).",
        )

    # ---- Store in database ----
    if profile_exists(db, username):
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail="Profile already exists.",
        )

    db.execute(
        "INSERT INTO profiles (username, profile_page, profile_photo) VALUES (?, ?, ?)",
        (username, profile_page, photo_bytes),
    )
    db.commit()

    return {"message": "Profile created successfully"}

@app.get("/profile/{username}", response_class=HTMLResponse)
def get_profile(username: str, db: sqlite3.Connection = Depends(get_db)):
    # Validate username format (same rules as creation)
    if not USERNAME_REGEX.fullmatch(username):
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND, detail="Profile not found"
        )

    cur = db.execute(
        "SELECT profile_page FROM profiles WHERE username = ?", (username,)
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND, detail="Profile not found"
        )
    # FastAPI will wrap the returned string in HTMLResponse due to response_class
    return row["profile_page"]

@app.get("/profile-photo/{username}")
def get_profile_photo(username: str, db: sqlite3.Connection = Depends(get_db)):
    # Validate username format
    if not USERNAME_REGEX.fullmatch(username):
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND, detail="Profile photo not found"
        )

    cur = db.execute(
        "SELECT profile_photo FROM profiles WHERE username = ?", (username,)
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND, detail="Profile photo not found"
        )
    photo_bytes = row["profile_photo"]
    return Response(content=photo_bytes, media_type="image/png")

# -------------------- Run Server --------------------
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=5000,
        reload=False,
    )