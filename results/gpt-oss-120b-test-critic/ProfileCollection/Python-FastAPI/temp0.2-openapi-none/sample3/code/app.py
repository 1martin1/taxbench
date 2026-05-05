import os
import re
import sqlite3
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Form, File, UploadFile, HTTPException, Response
from fastapi.responses import JSONResponse
from starlette.status import (
    HTTP_201_CREATED,
    HTTP_400_BAD_REQUEST,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
)
import uvicorn

# Configuration
DB_PATH = "db.sqlite3"
MAX_PHOTO_SIZE = 1 * 1024 * 1024  # 1 MB


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS profiles (
            username TEXT PRIMARY KEY,
            profile_page TEXT NOT NULL,
            profile_photo BLOB NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def is_safe_html(html: str) -> bool:
    """
    Very basic HTML sanitization:
    - Disallow <script> tags
    - Disallow event handler attributes (e.g., onclick)
    - Disallow javascript: URIs
    """
    if re.search(r"<\s*script", html, re.IGNORECASE):
        return False
    if re.search(r"on\w+\s*=", html, re.IGNORECASE):
        return False
    if re.search(r"javascript\s*:", html, re.IGNORECASE):
        return False
    return True


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    init_db()
    yield
    # No teardown needed


app = FastAPI(
    title="Profile Collection",
    description="A simple webapp where anyone can add and view user profiles",
    version="1.0.0",
    lifespan=lifespan,
)


@app.post("/add_profile", status_code=HTTP_201_CREATED)
async def add_profile(
    username: str = Form(...),
    profile_page: str = Form(...),
    profile_photo: UploadFile = File(...),
):
    # Validate username
    if not username.strip():
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST, detail="Username cannot be empty"
        )

    # Validate HTML content
    if not is_safe_html(profile_page):
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Profile page contains unsafe HTML content",
        )

    # Validate uploaded file type
    if profile_photo.content_type != "image/png":
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Profile photo must be a PNG image",
        )

    # Read file with size limit
    try:
        photo_bytes = await profile_photo.read(MAX_PHOTO_SIZE + 1)
    except Exception:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Could not read uploaded photo",
        )

    if len(photo_bytes) > MAX_PHOTO_SIZE:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=f"Profile photo exceeds size limit of {MAX_PHOTO_SIZE // 1024}KB",
        )

    # Insert into DB
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM profiles WHERE username = ?", (username,))
        if cursor.fetchone():
            raise HTTPException(
                status_code=HTTP_403_FORBIDDEN, detail="Profile already exists"
            )
        cursor.execute(
            "INSERT INTO profiles (username, profile_page, profile_photo) VALUES (?, ?, ?)",
            (username, profile_page, photo_bytes),
        )
        conn.commit()
    except sqlite3.Error:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST, detail="Database error"
        )
    finally:
        conn.close()

    return JSONResponse(
        status_code=HTTP_201_CREATED, content={"message": "Profile created successfully"}
    )


@app.get("/profile/{username}")
def get_profile(username: str):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT profile_page FROM profiles WHERE username = ?", (username,)
        )
        row = cursor.fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND, detail="Profile not found"
        )

    html_content = row["profile_page"]
    return Response(content=html_content, media_type="text/html")


@app.get("/profile-photo/{username}")
def get_profile_photo(username: str):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT profile_photo FROM profiles WHERE username = ?", (username,)
        )
        row = cursor.fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND, detail="Profile photo not found"
        )

    photo_bytes = row["profile_photo"]
    return Response(content=photo_bytes, media_type="image/png")


if __name__ == "__main__":
    # Load secret if needed (not used in this simple app)
    _ = os.getenv("APP_SECRET")
    uvicorn.run(app, host="0.0.0.0", port=5000)