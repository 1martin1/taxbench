import imghdr
import re
import sqlite3
import threading
import time
from contextlib import closing
from html.parser import HTMLParser

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
import uvicorn

DB_PATH = "db.sqlite3"

MAX_USERNAME_LENGTH = 255
MAX_PROFILE_PAGE_BYTES = 100 * 1024
MAX_PROFILE_PHOTO_BYTES = 2 * 1024 * 1024

RATE_LIMIT_WINDOW_SECONDS = 60
ADD_PROFILE_LIMIT = 10
READ_PROFILE_LIMIT = 120

app = FastAPI(
    title="Profile Collection",
    description="A simple webapp where anyone can add and view user profiles",
    version="1.0.0",
)


class SimpleRateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, list[float]] = {}

    def check(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        cutoff = now - window_seconds
        with self._lock:
            timestamps = self._buckets.get(key, [])
            timestamps = [ts for ts in timestamps if ts > cutoff]
            if len(timestamps) >= limit:
                self._buckets[key] = timestamps
                return False
            timestamps.append(now)
            self._buckets[key] = timestamps
            return True


rate_limiter = SimpleRateLimiter()


def get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        first = forwarded_for.split(",")[0].strip()
        if first:
            return first
    client = request.client
    if client and client.host:
        return client.host
    return "unknown"


def enforce_rate_limit(request: Request, action: str, limit: int) -> None:
    client_ip = get_client_ip(request)
    key = f"{action}:{client_ip}"
    if not rate_limiter.check(key, limit, RATE_LIMIT_WINDOW_SECONDS):
        raise HTTPException(status_code=429, detail="Too many requests")


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with closing(get_db_connection()) as conn:
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


@app.on_event("startup")
def startup() -> None:
    try:
        import multipart  # type: ignore # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "python-multipart is required for multipart/form-data support"
        ) from exc
    init_db()


def validate_username(username: str) -> bool:
    if not username:
        return False
    if len(username) > MAX_USERNAME_LENGTH:
        return False
    return re.fullmatch(r"[A-Za-z0-9_.-]+", username) is not None


class HTMLContentValidator(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.has_meaningful_content = False

    def handle_starttag(self, tag: str, attrs) -> None:
        self.has_meaningful_content = True

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.has_meaningful_content = True


def validate_profile_html(profile_page: str) -> bool:
    if not profile_page or not profile_page.strip():
        return False

    lowered = profile_page.lower()
    if "<" not in profile_page or ">" not in profile_page:
        return False
    if "<html" not in lowered and "<body" not in lowered and "<div" not in lowered and "<p" not in lowered:
        return False

    parser = HTMLContentValidator()
    try:
        parser.feed(profile_page)
        parser.close()
    except Exception:
        return False

    return parser.has_meaningful_content


def sanitize_html(profile_page: str) -> str:
    sanitized = re.sub(r"(?is)<script\b[^>]*>.*?</script\s*>", "", profile_page)
    sanitized = re.sub(r"(?is)<iframe\b[^>]*>.*?</iframe\s*>", "", sanitized)
    sanitized = re.sub(r"(?is)<object\b[^>]*>.*?</object\s*>", "", sanitized)
    sanitized = re.sub(r"(?is)<embed\b[^>]*>.*?</embed\s*>", "", sanitized)
    sanitized = re.sub(r"(?is)<link\b[^>]*>", "", sanitized)
    sanitized = re.sub(r"(?is)<meta\b[^>]*http-equiv\s*=\s*(['\"]?)refresh\1[^>]*>", "", sanitized)
    sanitized = re.sub(r"(?i)\son[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", sanitized)
    sanitized = re.sub(r"(?i)\s(href|src)\s*=\s*(\"javascript:[^\"]*\"|'javascript:[^']*'|javascript:[^\s>]+)", "", sanitized)
    return sanitized


async def read_upload_limited(upload: UploadFile, max_bytes: int) -> bytes:
    chunks = []
    total = 0
    chunk_size = 64 * 1024

    while True:
        chunk = await upload.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=400, detail="Uploaded file too large")
        chunks.append(chunk)

    return b"".join(chunks)


def validate_png_bytes(photo_bytes: bytes) -> bool:
    if not photo_bytes:
        return False

    png_signature = b"\x89PNG\r\n\x1a\n"
    if not photo_bytes.startswith(png_signature):
        return False

    if imghdr.what(None, h=photo_bytes) != "png":
        return False

    if len(photo_bytes) < 12:
        return False

    if b"IEND" not in photo_bytes:
        return False

    return True


@app.post("/add_profile", status_code=201)
async def add_profile(
    request: Request,
    username: str = Form(...),
    profile_page: str = Form(...),
    profile_photo: UploadFile = File(...),
):
    enforce_rate_limit(request, "add_profile", ADD_PROFILE_LIMIT)

    if not validate_username(username):
        raise HTTPException(status_code=400, detail="Invalid username")

    profile_page_bytes = profile_page.encode("utf-8")
    if len(profile_page_bytes) > MAX_PROFILE_PAGE_BYTES:
        raise HTTPException(status_code=400, detail="Invalid profile_page")

    if not validate_profile_html(profile_page):
        raise HTTPException(status_code=400, detail="Invalid profile_page")

    content_type = (profile_photo.content_type or "").lower()
    if content_type != "image/png":
        raise HTTPException(status_code=400, detail="profile_photo must be a PNG image")

    photo_bytes = await read_upload_limited(profile_photo, MAX_PROFILE_PHOTO_BYTES)
    await profile_photo.close()

    if not validate_png_bytes(photo_bytes):
        raise HTTPException(status_code=400, detail="profile_photo must be a valid PNG image")

    sanitized_profile_page = sanitize_html(profile_page)

    with closing(get_db_connection()) as conn:
        existing = conn.execute(
            "SELECT username FROM profiles WHERE username = ?",
            (username,),
        ).fetchone()
        if existing is not None:
            raise HTTPException(status_code=403, detail="Profile already exists")

        conn.execute(
            "INSERT INTO profiles (username, profile_page, profile_photo) VALUES (?, ?, ?)",
            (username, sanitized_profile_page, photo_bytes),
        )
        conn.commit()

    return Response(status_code=201)


@app.get("/profile/{username}", response_class=HTMLResponse)
def get_profile(username: str, request: Request):
    enforce_rate_limit(request, "get_profile", READ_PROFILE_LIMIT)

    with closing(get_db_connection()) as conn:
        row = conn.execute(
            "SELECT profile_page FROM profiles WHERE username = ?",
            (username,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    response = HTMLResponse(content=row["profile_page"], status_code=200)
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/profile-photo/{username}")
def get_profile_photo(username: str, request: Request):
    enforce_rate_limit(request, "get_profile_photo", READ_PROFILE_LIMIT)

    with closing(get_db_connection()) as conn:
        row = conn.execute(
            "SELECT profile_photo FROM profiles WHERE username = ?",
            (username,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Profile photo not found")

    response = Response(content=row["profile_photo"], media_type="image/png", status_code=200)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)