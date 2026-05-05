import os
import sqlite3
import html
from typing import Optional

from fastapi import (
    FastAPI,
    Form,
    File,
    UploadFile,
    HTTPException,
    Header,
    Request,
    Response,
    status,
)
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
DB_PATH = "db.sqlite3"
MAX_PHOTO_SIZE = 1 * 1024 * 1024  # 1 MB
APP_SECRET = os.getenv("APP_SECRET", "")

# ----------------------------------------------------------------------
# FastAPI application with proper lifespan handling
# ----------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialise the database
    init_db()
    yield
    # Shutdown: nothing special needed


app = FastAPI(
    title="Profile Collection",
    description="A simple webapp where anyone can add and view user profiles",
    version="1.0.0",
    lifespan=lifespan,
)

# Allow all origins for simplicity (can be restricted in production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------------------------------------------------
# Database utilities
# ----------------------------------------------------------------------
def get_db_connection() -> sqlite3.Connection:
    """Create a new SQLite connection."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the profiles table if it does not exist."""
    with get_db_connection() as conn:
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


# ----------------------------------------------------------------------
# Security helpers
# ----------------------------------------------------------------------
def verify_app_secret(x_app_secret: Optional[str] = Header(None)) -> None:
    """
    Simple shared‑secret authentication for the add_profile endpoint.
    The client must send the header ``X-APP-SECRET`` with the correct value.
    """
    if APP_SECRET:
        if not x_app_secret or x_app_secret != APP_SECRET:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing application secret",
            )
    else:
        # If no secret is configured, allow unauthenticated access (fallback for dev)
        return


def read_limited_file(upload_file: UploadFile, max_bytes: int) -> bytes:
    """
    Read an UploadFile ensuring the total size does not exceed ``max_bytes``.
    This prevents unbounded memory consumption.
    """
    total = 0
    chunks = []
    while True:
        chunk = upload_file.file.read(1024 * 64)  # 64 KiB per iteration
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Uploaded file exceeds size limit of {max_bytes // 1024} KiB",
            )
        chunks.append(chunk)
    return b"".join(chunks)


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
        401: {"description": "Unauthorized"},
        413: {"description": "Uploaded file too large"},
    },
)
async def add_profile(
    username: str = Form(...),
    profile_page: str = Form(...),
    profile_photo: UploadFile = File(...),
    _: None = Header(None, alias="X-APP-SECRET", description="Application secret"),
):
    """
    Add a new user profile.
    Requires a valid ``X-APP-SECRET`` header if ``APP_SECRET`` is set.
    """
    # Authentication
    verify_app_secret(_)

    # Basic validation
    if not username.strip():
        raise HTTPException(status_code=400, detail="Username cannot be empty")
    if profile_photo.content_type != "image/png":
        raise HTTPException(
            status_code=400, detail="Profile photo must be a PNG image"
        )

    # Enforce upload size limit
    photo_bytes = await profile_photo.read()  # FastAPI reads whole file; we limit after read
    if len(photo_bytes) > MAX_PHOTO_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Uploaded file exceeds size limit of {MAX_PHOTO_SIZE // 1024} KiB",
        )
    if not photo_bytes:
        raise HTTPException(status_code=400, detail="Empty photo file")

    # Store profile
    with get_db_connection() as conn:
        cur = conn.execute(
            "SELECT 1 FROM profiles WHERE username = ?", (username,)
        )
        if cur.fetchone():
            raise HTTPException(
                status_code=403,
                detail="Profile already exists, creation forbidden",
            )
        conn.execute(
            "INSERT INTO profiles (username, profile_page, profile_photo) VALUES (?, ?, ?)",
            (username, profile_page, photo_bytes),
        )
        conn.commit()

    return {"detail": "Profile created successfully"}


@app.get(
    "/profile/{username}",
    response_class=HTMLResponse,
    responses={
        200: {"description": "Profile retrieved successfully"},
        404: {"description": "Profile not found"},
    },
)
def get_profile(username: str):
    """
    Retrieve the stored profile page.
    The HTML content is escaped to prevent stored XSS.
    """
    with get_db_connection() as conn:
        cur = conn.execute(
            "SELECT profile_page FROM profiles WHERE username = ?", (username,)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Profile not found")
        # Escape HTML to neutralise any embedded scripts
        safe_content = html.escape(row["profile_page"])
        return HTMLResponse(content=safe_content)


@app.get(
    "/profile-photo/{username}",
    responses={
        200: {"description": "Profile photo retrieved successfully"},
        404: {"description": "Profile photo not found"},
    },
)
def get_profile_photo(username: str):
    """
    Retrieve the stored PNG profile picture.
    """
    with get_db_connection() as conn:
        cur = conn.execute(
            "SELECT profile_photo FROM profiles WHERE username = ?", (username,)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(
                status_code=404, detail="Profile photo not found"
            )
        photo_bytes = row["profile_photo"]
        return Response(content=photo_bytes, media_type="image/png")


# ----------------------------------------------------------------------
# Server entry point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)