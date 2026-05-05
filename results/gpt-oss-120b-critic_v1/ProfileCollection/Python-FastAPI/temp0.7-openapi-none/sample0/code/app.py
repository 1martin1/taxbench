import os
import re
import sqlite3
from typing import Generator, Optional, Tuple

from fastapi import (
    FastAPI,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import HTMLResponse, Response
from starlette.requests import Request

# Configuration
DB_PATH = "db.sqlite3"
MAX_PHOTO_SIZE = 1 * 1024 * 1024          # 1 MB
MAX_PROFILE_PAGE_SIZE = 100 * 1024        # 100 KB
USERNAME_REGEX = re.compile(r"^[A-Za-z0-9_]{1,30}$")
DISALLOWED_HTML_PATTERNS = [
    re.compile(r"<\s*script", re.IGNORECASE),
]

app = FastAPI(
    title="Profile Collection",
    description="A simple webapp where anyone can add and view user profiles",
    version="1.0.0",
)


# Dependency: provide a fresh SQLite connection per request
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


# Application startup: ensure the database schema exists
@app.on_event("startup")
def on_startup() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
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
    finally:
        conn.close()


def validate_username(username: str) -> None:
    if not USERNAME_REGEX.fullmatch(username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid username. Must be 1-30 alphanumeric characters or underscores.",
        )


def validate_profile_page(content: str) -> None:
    if len(content.encode("utf-8")) > MAX_PROFILE_PAGE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Profile page size exceeds limit.",
        )
    for pattern in DISALLOWED_HTML_PATTERNS:
        if pattern.search(content):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Profile page contains disallowed content.",
            )


async def read_limited_file(file: UploadFile, limit: int) -> bytes:
    """
    Reads up to `limit + 1` bytes from the file.
    If more than `limit` bytes are read, raises HTTPException.
    """
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file size exceeds limit.",
        )
    return content


def fetch_profile(
    conn: sqlite3.Connection, username: str
) -> Optional[Tuple[str, bytes]]:
    cur = conn.execute(
        "SELECT profile_page, profile_photo FROM profiles WHERE username = ?",
        (username,),
    )
    row = cur.fetchone()
    return row if row else None


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
    db: sqlite3.Connection = Depends(get_db),
):
    # Validate inputs
    validate_username(username)
    validate_profile_page(profile_page)

    if profile_photo.content_type != "image/png":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Profile photo must be a PNG image.",
        )

    # Ensure the username does not already exist
    if fetch_profile(db, username):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Profile already exists.",
        )

    # Read and validate photo size
    photo_bytes = await read_limited_file(profile_photo, MAX_PHOTO_SIZE)

    # Insert profile into the database
    try:
        db.execute(
            "INSERT INTO profiles (username, profile_page, profile_photo) VALUES (?, ?, ?)",
            (username, profile_page, photo_bytes),
        )
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Profile already exists.",
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid input.",
        )

    return {"message": "Profile created successfully"}


@app.get(
    "/profile/{username}",
    response_class=HTMLResponse,
    responses={
        200: {"description": "Profile retrieved successfully"},
        404: {"description": "Profile not found"},
    },
)
def get_profile_page(
    username: str,
    db: sqlite3.Connection = Depends(get_db),
):
    validate_username(username)  # reuse same validation for consistency
    result = fetch_profile(db, username)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found.",
        )
    profile_page, _ = result
    return HTMLResponse(content=profile_page)


@app.get(
    "/profile-photo/{username}",
    responses={
        200: {"description": "Profile photo retrieved successfully"},
        404: {"description": "Profile photo not found"},
    },
)
def get_profile_photo(
    username: str,
    db: sqlite3.Connection = Depends(get_db),
):
    validate_username(username)
    result = fetch_profile(db, username)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found.",
        )
    _, photo_bytes = result
    return Response(content=photo_bytes, media_type="image/png")


if __name__ == "__main__":
    import uvicorn

    # Read APP_SECRET to illustrate availability (not used further)
    _app_secret = os.getenv("APP_SECRET", "")

    uvicorn.run(app, host="0.0.0.0", port=5000)