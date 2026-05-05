import os
import re
import sqlite3
from typing import Optional

from fastapi import FastAPI, Form, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
import uvicorn

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET")  # Currently unused but kept for future use

# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------
def init_db() -> None:
    """Create the profiles table if it does not exist."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cur = conn.cursor()
    cur.execute(
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


def _validate_png_signature(data: bytes) -> bool:
    """Check that the first 8 bytes match the PNG signature."""
    png_signature = b"\x89PNG\r\n\x1a\n"
    return data.startswith(png_signature)


def _validate_html_content(html: str) -> Optional[str]:
    """
    Very basic HTML validation to mitigate stored XSS.
    Rejects content containing <script> tags or javascript: URIs.
    Returns an error message if validation fails, otherwise None.
    """
    lowered = html.lower()
    if "<script" in lowered:
        return "HTML content must not contain <script> tags."
    if "javascript:" in lowered:
        return "HTML content must not contain javascript: URIs."
    # Additional simple checks can be added here.
    return None


# ----------------------------------------------------------------------
# FastAPI application with lifespan for startup
# ----------------------------------------------------------------------
app = FastAPI()


@app.on_event("startup")
def on_startup() -> None:
    """Legacy startup event kept for compatibility; delegates to init_db."""
    init_db()


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------
@app.post(
    "/add_profile",
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Profile created successfully"},
        403: {"description": "Profile already exists, creation forbidden"},
        400: {"description": "Invalid input"},
    },
)
async def add_profile(
    username: str = Form(...),
    profile_page: str = Form(...),
    profile_photo: UploadFile = File(...),
):
    """
    Add a new user profile.
    Expects multipart/form-data with fields:
    - username (string)
    - profile_page (HTML string)
    - profile_photo (PNG file)
    """
    # ------------------------------------------------------------------
    # Validate MIME type (client‑provided) first
    # ------------------------------------------------------------------
    if profile_photo.content_type != "image/png":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Profile photo must be a PNG image.",
        )

    # ------------------------------------------------------------------
    # Read file content (awaitable) and validate PNG signature
    # ------------------------------------------------------------------
    photo_bytes = await profile_photo.read()
    if not _validate_png_signature(photo_bytes):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is not a valid PNG image.",
        )

    # ------------------------------------------------------------------
    # Validate HTML content to reduce XSS risk
    # ------------------------------------------------------------------
    html_error = _validate_html_content(profile_page)
    if html_error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=html_error,
        )

    # ------------------------------------------------------------------
    # Insert into the database using a thread pool to avoid blocking
    # ------------------------------------------------------------------
    async def db_insert():
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO profiles (username, profile_page, profile_photo) VALUES (?, ?, ?)",
                (username, profile_page, photo_bytes),
            )
            conn.commit()
        finally:
            conn.close()

    try:
        await run_in_threadpool(db_insert)
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Profile already exists.",
        )

    return {"message": "Profile created successfully"}


@app.get(
    "/profile/{username}",
    responses={
        200: {"description": "Profile retrieved successfully", "content": {"text/html": {}}},
        404: {"description": "Profile not found"},
    },
)
async def get_profile(username: str):
    """
    Retrieve the HTML profile page for a given username.
    """
    async def db_fetch():
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT profile_page FROM profiles WHERE username = ?",
                (username,),
            )
            return cur.fetchone()
        finally:
            conn.close()

    row = await run_in_threadpool(db_fetch)

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found.",
        )

    html_content = row[0]
    return Response(content=html_content, media_type="text/html")


@app.get(
    "/profile-photo/{username}",
    responses={
        200: {"description": "Profile photo retrieved successfully", "content": {"image/png": {}}},
        404: {"description": "Profile photo not found"},
    },
)
async def get_profile_photo(username: str):
    """
    Retrieve the PNG profile photo for a given username.
    """
    async def db_fetch():
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT profile_photo FROM profiles WHERE username = ?",
                (username,),
            )
            return cur.fetchone()
        finally:
            conn.close()

    row = await run_in_threadpool(db_fetch)

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile photo not found.",
        )

    photo_bytes = row[0]
    return Response(content=photo_bytes, media_type="image/png")


# ----------------------------------------------------------------------
# Run the application
# ----------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)