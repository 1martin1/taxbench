import os
import sqlite3
from typing import Any, Tuple

from fastapi import (
    FastAPI,
    Form,
    File,
    UploadFile,
    HTTPException,
    Response,
    status,
)
from starlette.concurrency import run_in_threadpool

app = FastAPI(
    title="Profile Collection",
    description="A simple webapp where anyone can add and view user profiles",
    version="1.0.0",
)

DB_PATH = "db.sqlite3"

# ---------- Configuration ----------
MAX_PROFILE_PAGE_SIZE = 10 * 1024          # 10 KB
MAX_PHOTO_SIZE = 1 * 1024 * 1024          # 1 MB
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


# ---------- Database utilities ----------
def _get_db_connection() -> sqlite3.Connection:
    """Create a new SQLite connection. Each thread gets its own connection."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    conn = _get_db_connection()
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


async def _db_fetch_one(query: str, params: Tuple[Any, ...] = ()) -> sqlite3.Row | None:
    """Execute a SELECT that returns at most one row."""
    def _inner():
        conn = _get_db_connection()
        try:
            cur = conn.execute(query, params)
            return cur.fetchone()
        finally:
            conn.close()
    return await run_in_threadpool(_inner)


async def _db_execute(query: str, params: Tuple[Any, ...] = ()) -> None:
    """Execute an INSERT/UPDATE/DELETE."""
    def _inner():
        conn = _get_db_connection()
        try:
            conn.execute(query, params)
            conn.commit()
        finally:
            conn.close()
    await run_in_threadpool(_inner)


# ---------- Validation helpers ----------
def _validate_username(username: str) -> None:
    if not username or not username.strip():
        raise HTTPException(status_code=400, detail="Username cannot be empty")
    if len(username) > 150:
        raise HTTPException(status_code=400, detail="Username too long")


def _validate_profile_page(html: str) -> None:
    if len(html.encode("utf-8")) > MAX_PROFILE_PAGE_SIZE:
        raise HTTPException(status_code=400, detail="Profile page too large")
    lowered = html.lower()
    if "<script" in lowered or "javascript:" in lowered:
        raise HTTPException(status_code=400, detail="Profile page contains disallowed content")


def _validate_png_signature(data: bytes) -> None:
    if not data.startswith(PNG_SIGNATURE):
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid PNG image")


async def _read_upload_file_limited(upload: UploadFile, max_size: int) -> bytes:
    """Read an UploadFile ensuring it does not exceed max_size bytes."""
    size = 0
    chunks = []
    while True:
        chunk = await upload.read(1024 * 64)  # 64KB per chunk
        if not chunk:
            break
        size += len(chunk)
        if size > max_size:
            raise HTTPException(status_code=400, detail="Uploaded file exceeds size limit")
        chunks.append(chunk)
    return b"".join(chunks)


# ---------- Application lifecycle ----------
@app.on_event("startup")
def on_startup() -> None:
    _init_db()
    # Load APP_SECRET if needed (not used in this example)
    _ = os.getenv("APP_SECRET")


# ---------- Endpoints ----------
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
    # Validate inputs
    _validate_username(username)
    _validate_profile_page(profile_page)

    if profile_photo.content_type != "image/png":
        raise HTTPException(status_code=400, detail="Profile photo must be a PNG image")

    photo_bytes = await _read_upload_file_limited(profile_photo, MAX_PHOTO_SIZE)
    _validate_png_signature(photo_bytes)

    # Check existence
    existing = await _db_fetch_one("SELECT 1 FROM profiles WHERE username = ?", (username,))
    if existing:
        raise HTTPException(status_code=403, detail="Profile already exists")

    # Insert profile
    await _db_execute(
        "INSERT INTO profiles (username, profile_page, profile_photo) VALUES (?, ?, ?)",
        (username, profile_page, photo_bytes),
    )
    return Response(status_code=201)


@app.get(
    "/profile/{username}",
    responses={
        200: {"description": "Profile retrieved successfully", "content": {"text/html": {}}},
        404: {"description": "Profile not found"},
    },
)
async def get_profile(username: str):
    _validate_username(username)  # reuse same validation (no empty)

    row = await _db_fetch_one(
        "SELECT profile_page FROM profiles WHERE username = ?", (username,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Profile not found")
    html_content = row["profile_page"]
    # The content was validated on insertion, safe to serve as HTML
    return Response(content=html_content, media_type="text/html")


@app.get(
    "/profile-photo/{username}",
    responses={
        200: {"description": "Profile photo retrieved successfully", "content": {"image/png": {}}},
        404: {"description": "Profile photo not found"},
    },
)
async def get_profile_photo(username: str):
    _validate_username(username)

    row = await _db_fetch_one(
        "SELECT profile_photo FROM profiles WHERE username = ?", (username,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Profile photo not found")
    photo_bytes = row["profile_photo"]
    return Response(content=photo_bytes, media_type="image/png")


# ---------- Run server ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)