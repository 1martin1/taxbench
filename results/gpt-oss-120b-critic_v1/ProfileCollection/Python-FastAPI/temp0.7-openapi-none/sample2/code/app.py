import os
import re
import sqlite3
import threading
from typing import Generator

from fastapi import (
    FastAPI,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    Response,
)
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.status import (
    HTTP_201_CREATED,
    HTTP_400_BAD_REQUEST,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
)
import uvicorn

# -------------------------------------------------------------------------
# Configuration & Constants
# -------------------------------------------------------------------------
DB_PATH = "db.sqlite3"

MAX_USERNAME_LEN = 50               # characters
MAX_PROFILE_PAGE_LEN = 10 * 1024    # 10 KiB
MAX_PHOTO_SIZE = 2 * 1024 * 1024    # 2 MiB
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# -------------------------------------------------------------------------
# Application setup
# -------------------------------------------------------------------------
app = FastAPI(
    title="Profile Collection",
    description="A simple webapp where anyone can add and view user profiles",
    version="1.0.0",
)

# Secure CORS: allow any origin but do NOT allow credentials.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------------------------
# Database utilities
# -------------------------------------------------------------------------
_write_lock = threading.Lock()


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """
    Dependency that provides a SQLite connection.
    The connection is closed after the request is processed.
    """
    conn = sqlite3.connect(
        DB_PATH, timeout=30, check_same_thread=False, isolation_level=None
    )
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """
    Initialize the SQLite database and ensure the profiles table exists.
    Exceptions are allowed to propagate so startup fails loudly if needed.
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                username TEXT PRIMARY KEY,
                html_content TEXT NOT NULL,
                photo BLOB NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


@app.on_event("startup")
def on_startup() -> None:
    init_db()


# -------------------------------------------------------------------------
# Helper functions
# -------------------------------------------------------------------------
def sanitize_html(html: str) -> str:
    """
    Very basic sanitization:
    - Remove <script> tags (case‑insensitive) and their content.
    - Remove event handler attributes (on*).
    This is not a full HTML sanitizer but mitigates stored XSS for this demo.
    """
    # Remove script tags
    html = re.sub(
        r"(?i)<\s*script[^>]*>.*?<\s*/\s*script\s*>", "", html, flags=re.DOTALL
    )
    # Remove event handler attributes like onclick="..."
    html = re.sub(r'(?i)\s+on\w+\s*=\s*"[^"]*"', "", html)
    html = re.sub(r"(?i)\s+on\w+\s*=\s*'[^']*'", "", html)
    return html


async def read_limited_file(file: UploadFile, max_size: int) -> bytes:
    """
    Read the uploaded file up to max_size bytes.
    If the file exceeds max_size, raise HTTPException.
    """
    size = 0
    chunks = []
    while True:
        chunk = await file.read(1024 * 64)  # 64 KiB per chunk
        if not chunk:
            break
        size += len(chunk)
        if size > max_size:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail=f"Uploaded file exceeds size limit of {max_size // (1024 * 1024)} MiB",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def validate_png(data: bytes) -> None:
    """
    Verify that the provided bytes start with the PNG magic number.
    """
    if not data.startswith(PNG_MAGIC):
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Uploaded file is not a valid PNG image",
        )


# -------------------------------------------------------------------------
# Endpoints
# -------------------------------------------------------------------------
@app.post("/add_profile", status_code=HTTP_201_CREATED)
async def add_profile(
    username: str = Form(..., description="The username for the profile"),
    profile_page: str = Form(..., description="The HTML content for the profile page"),
    profile_photo: UploadFile = File(..., description="The PNG profile picture"),
    db: sqlite3.Connection = Depends(get_db),
):
    # Input length validation
    if not username or len(username) > MAX_USERNAME_LEN:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=f"Username is required and must be at most {MAX_USERNAME_LEN} characters",
        )
    if len(profile_page) > MAX_PROFILE_PAGE_LEN:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=f"Profile page content exceeds {MAX_PROFILE_PAGE_LEN // 1024} KiB limit",
        )

    # Basic content‑type check (client side)
    if profile_photo.content_type != "image/png":
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Profile photo must be a PNG image",
        )

    # Read file with size limit
    photo_bytes = await read_limited_file(profile_photo, MAX_PHOTO_SIZE)

    # Verify PNG magic bytes
    validate_png(photo_bytes)

    # Sanitize HTML
    sanitized_html = sanitize_html(profile_page)

    # Check for existing username
    cur = db.execute("SELECT 1 FROM profiles WHERE username = ?", (username,))
    if cur.fetchone():
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail="Profile already exists, creation forbidden",
        )

    # Insert new profile inside a write lock to avoid SQLite write contention
    with _write_lock:
        try:
            db.execute(
                "INSERT INTO profiles (username, html_content, photo) VALUES (?, ?, ?)",
                (username, sanitized_html, photo_bytes),
            )
            db.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=HTTP_403_FORBIDDEN,
                detail="Profile already exists, creation forbidden",
            )

    return {"detail": "Profile created successfully"}


@app.get("/profile/{username}", response_class=HTMLResponse)
def get_profile(username: str, db: sqlite3.Connection = Depends(get_db)):
    cur = db.execute(
        "SELECT html_content FROM profiles WHERE username = ?",
        (username,),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND, detail="Profile not found"
        )
    html_content = row["html_content"]
    # Return as HTML; content was sanitized on insert.
    return HTMLResponse(content=html_content, status_code=200)


@app.get("/profile-photo/{username}")
def get_profile_photo(username: str, db: sqlite3.Connection = Depends(get_db)):
    cur = db.execute(
        "SELECT photo FROM profiles WHERE username = ?",
        (username,),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND, detail="Profile photo not found"
        )
    photo_bytes = row["photo"]
    return Response(content=photo_bytes, media_type="image/png", status_code=200)


if __name__ == "__main__":
    # Run the application directly; using the app object avoids module name issues.
    uvicorn.run(app, host="0.0.0.0", port=5000, reload=False)