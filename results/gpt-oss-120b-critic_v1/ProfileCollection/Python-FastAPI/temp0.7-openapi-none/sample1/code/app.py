import os
import re
import sqlite3
import threading
import logging
import asyncio

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Response, Request
from fastapi.responses import JSONResponse
import uvicorn

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Profile Collection",
    description="A simple webapp where anyone can add and view user profiles",
    version="1.0.0",
)

# Load secret (do not fall back to an insecure default)
APP_SECRET = os.getenv("APP_SECRET")
if APP_SECRET is None:
    logger.warning("APP_SECRET environment variable is not set; proceeding without it.")

# Constants
MAX_PHOTO_SIZE = 2 * 1024 * 1024  # 2 MB
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# ---------------------------------------------------------------------------
# Database utilities
# ---------------------------------------------------------------------------

def init_db(conn: sqlite3.Connection) -> None:
    """Create the profiles table if it does not exist."""
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


def get_db_connection() -> sqlite3.Connection:
    """Return a thread‑safe SQLite connection."""
    # check_same_thread=False allows sharing the connection across threads
    return sqlite3.connect("db.sqlite3", check_same_thread=False)


@app.on_event("startup")
def on_startup() -> None:
    # Initialise a single shared connection and a lock for thread safety
    app.state.db = get_db_connection()
    app.state.db_lock = threading.Lock()
    init_db(app.state.db)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def is_valid_png(data: bytes) -> bool:
    """Very basic PNG validation using the file signature."""
    return data.startswith(PNG_SIGNATURE)


def is_safe_html(html: str) -> bool:
    """Reject HTML containing <script> tags (case‑insensitive)."""
    return re.search(r"<\s*script", html, re.IGNORECASE) is None


# ---------------------------------------------------------------------------
# Async wrappers around blocking DB operations
# ---------------------------------------------------------------------------

async def db_insert_profile(username: str, page: str, photo: bytes) -> None:
    def _insert():
        with app.state.db_lock:
            try:
                app.state.db.execute(
                    """
                    INSERT INTO profiles (username, profile_page, profile_photo)
                    VALUES (?, ?, ?)
                    """,
                    (username, page, photo),
                )
                app.state.db.commit()
            except sqlite3.IntegrityError:
                raise HTTPException(status_code=403, detail="Profile already exists")
    await asyncio.to_thread(_insert)


async def db_get_profile_page(username: str) -> str:
    def _select():
        with app.state.db_lock:
            cur = app.state.db.execute(
                """
                SELECT profile_page FROM profiles WHERE username = ?
                """,
                (username,),
            )
            row = cur.fetchone()
            return row[0] if row else None
    result = await asyncio.to_thread(_select)
    if result is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return result


async def db_get_profile_photo(username: str) -> bytes:
    def _select():
        with app.state.db_lock:
            cur = app.state.db.execute(
                """
                SELECT profile_photo FROM profiles WHERE username = ?
                """,
                (username,),
            )
            row = cur.fetchone()
            return row[0] if row else None
    result = await asyncio.to_thread(_select)
    if result is None:
        raise HTTPException(status_code=404, detail="Profile photo not found")
    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/add_profile", status_code=201)
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
    # Basic content‑type check
    if profile_photo.content_type != "image/png":
        raise HTTPException(status_code=400, detail="Profile photo must be a PNG image")

    # Read the uploaded file with size limit
    try:
        photo_bytes = await profile_photo.read(MAX_PHOTO_SIZE + 1)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to read uploaded file")

    if len(photo_bytes) > MAX_PHOTO_SIZE:
        raise HTTPException(status_code=400, detail="Profile photo exceeds size limit of 2 MB")

    # Validate PNG signature
    if not is_valid_png(photo_bytes):
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid PNG image")

    # Simple HTML safety check
    if not is_safe_html(profile_page):
        raise HTTPException(status_code=400, detail="Profile page contains forbidden content")

    # Insert into DB
    try:
        await db_insert_profile(username, profile_page, photo_bytes)
    except HTTPException:
        # Re‑raise known HTTP errors (e.g., duplicate)
        raise
    except Exception as exc:
        logger.exception("Unexpected error while inserting profile")
        raise HTTPException(status_code=500, detail="Internal server error")

    return {"message": "Profile created successfully"}


@app.get("/profile/{username}")
async def get_profile(username: str):
    """
    Retrieve the HTML content for a given username.
    """
    try:
        html_content = await db_get_profile_page(username)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Unexpected error while fetching profile page")
        raise HTTPException(status_code=500, detail="Internal server error")

    return Response(content=html_content, media_type="text/html")


@app.get("/profile-photo/{username}")
async def get_profile_photo(username: str):
    """
    Retrieve the PNG profile picture for a given username.
    """
    try:
        photo_bytes = await db_get_profile_photo(username)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Unexpected error while fetching profile photo")
        raise HTTPException(status_code=500, detail="Internal server error")

    return Response(content=photo_bytes, media_type="image/png")


# ---------------------------------------------------------------------------
# Global exception handler to avoid leaking internal details
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


if __name__ == "__main__":
    # Run the application on 0.0.0.0:5000 as requested
    uvicorn.run(app, host="0.0.0.0", port=5000)