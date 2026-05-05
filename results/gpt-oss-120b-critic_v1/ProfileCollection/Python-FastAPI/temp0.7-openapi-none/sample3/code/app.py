import os
import re
import sqlite3
import threading

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status, Depends
from fastapi.responses import HTMLResponse, Response

# -----------------------------
# Configuration and Constants
# -----------------------------
DB_PATH = "db.sqlite3"
MAX_HTML_SIZE = 100 * 1024          # 100 KiB
MAX_PHOTO_SIZE = 2 * 1024 * 1024   # 2 MiB
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# -----------------------------
# Global DB Connection & Lock
# -----------------------------
db_lock = threading.Lock()
db_conn: sqlite3.Connection | None = None


def init_db() -> None:
    """Initialize the SQLite database and ensure the required table exists."""
    global db_conn
    # Create a single connection that can be used from multiple threads
    db_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    db_conn.row_factory = sqlite3.Row
    with db_conn:
        db_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                username TEXT PRIMARY KEY,
                html TEXT NOT NULL,
                photo BLOB NOT NULL
            )
            """
        )


def get_db() -> sqlite3.Connection:
    """Dependency that provides the global DB connection."""
    if db_conn is None:
        raise RuntimeError("Database not initialized")
    return db_conn


# -----------------------------
# Utility Functions
# -----------------------------
def sanitize_html(html: str) -> str:
    """
    Very lightweight HTML sanitisation to mitigate stored XSS.
    - Removes <script>...</script> blocks (case‑insensitive).
    - Strips event‑handler attributes like onclick, onload, etc.
    This is not a full‑featured sanitizer but reduces obvious attack vectors.
    """
    # Remove <script> tags and their content
    html = re.sub(r"(?i)<script.*?>.*?</script>", "", html, flags=re.DOTALL)

    # Remove on* attributes (e.g., onclick, onload)
    html = re.sub(r'(?i)\s+on\w+\s*=\s*"[^"]*"', "", html)
    html = re.sub(r"(?i)\s+on\w+\s*=\s*'[^']*'", "", html)

    return html


def validate_png(data: bytes) -> bool:
    """Check that the uploaded bytes start with the PNG signature."""
    return data.startswith(PNG_SIGNATURE)


# -----------------------------
# FastAPI Application
# -----------------------------
app = FastAPI(
    title="Profile Collection",
    description="A simple webapp where anyone can add and view user profiles",
    version="1.0.0",
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


# ----------------------------------------------------------------------
# Endpoint: Add a new profile
# ----------------------------------------------------------------------
@app.post("/add_profile", status_code=status.HTTP_201_CREATED)
async def add_profile(
    username: str = Form(..., description="The username for the profile"),
    profile_page: str = Form(..., description="The HTML content for the profile page"),
    profile_photo: UploadFile = File(..., description="The PNG profile picture"),
    db: sqlite3.Connection = Depends(get_db),
):
    # ---- Basic validation ----
    if not username.strip():
        raise HTTPException(status_code=400, detail="Username cannot be empty")

    # Enforce HTML size limit
    if len(profile_page.encode("utf-8")) > MAX_HTML_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Profile page exceeds size limit of {MAX_HTML_SIZE // 1024} KiB",
        )

    # Enforce photo size limit while reading (read up to limit + 1 byte)
    try:
        photo_bytes = await profile_photo.read(MAX_PHOTO_SIZE + 1)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to read profile photo")

    if len(photo_bytes) > MAX_PHOTO_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Profile photo exceeds size limit of {MAX_PHOTO_SIZE // (1024 * 1024)} MiB",
        )

    # Verify PNG format using magic bytes
    if not validate_png(photo_bytes):
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid PNG image")

    # Sanitize HTML to reduce XSS risk
    safe_html = sanitize_html(profile_page)

    # ---- Database operations (with thread‑safe lock) ----
    with db_lock:
        # Check if the username already exists
        cur = db.execute("SELECT 1 FROM profiles WHERE username = ?", (username,))
        if cur.fetchone():
            raise HTTPException(status_code=403, detail="Profile already exists")

        try:
            db.execute(
                "INSERT INTO profiles (username, html, photo) VALUES (?, ?, ?)",
                (username, safe_html, photo_bytes),
            )
            db.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=403, detail="Profile already exists")
        except Exception:
            raise HTTPException(status_code=500, detail="Database error")

    return {"message": "Profile created successfully"}


# ----------------------------------------------------------------------
# Endpoint: Retrieve profile HTML
# ----------------------------------------------------------------------
@app.get(
    "/profile/{username}",
    response_class=HTMLResponse,
    responses={404: {"description": "Profile not found"}},
)
def get_profile(username: str, db: sqlite3.Connection = Depends(get_db)):
    with db_lock:
        cur = db.execute("SELECT html FROM profiles WHERE username = ?", (username,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Profile not found")
    return HTMLResponse(content=row["html"], status_code=200)


# ----------------------------------------------------------------------
# Endpoint: Retrieve profile photo (PNG)
# ----------------------------------------------------------------------
@app.get(
    "/profile-photo/{username}",
    responses={
        200: {"content": {"image/png": {}}},
        404: {"description": "Profile photo not found"},
    },
)
def get_profile_photo(username: str, db: sqlite3.Connection = Depends(get_db)):
    with db_lock:
        cur = db.execute("SELECT photo FROM profiles WHERE username = ?", (username,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Profile photo not found")
    return Response(content=row["photo"], media_type="image/png", status_code=200)


# ----------------------------------------------------------------------
# Run the application
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    # The secret is read here per the original specification, though not used.
    _app_secret = os.getenv("APP_SECRET", "")

    uvicorn.run(app, host="0.0.0.0", port=5000)