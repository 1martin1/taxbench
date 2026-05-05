import os
import re
import sqlite3
import threading
from contextlib import closing
from html.parser import HTMLParser
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
import uvicorn


DB_PATH = "db.sqlite3"

MAX_PROFILE_PAGE_BYTES = 100 * 1024
MAX_PROFILE_PHOTO_BYTES = 5 * 1024 * 1024
MAX_REQUEST_BODY_BYTES = MAX_PROFILE_PAGE_BYTES + MAX_PROFILE_PHOTO_BYTES + 64 * 1024
MAX_CONCURRENT_REQUESTS = 50

USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_\-\.]+")

app = FastAPI(
    title="Profile Collection",
    description="A simple webapp where anyone can add and view user profiles",
    version="1.0.0",
)

_request_semaphore = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)


class BasicHTMLValidator(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.seen_tag = False
        self.invalid = False

    def handle_starttag(self, tag: str, attrs) -> None:
        self.seen_tag = True
        if tag.lower() in {"script", "iframe", "object", "embed", "link", "meta"}:
            self.invalid = True
            return
        for attr_name, _attr_value in attrs:
            if attr_name and attr_name.lower().startswith("on"):
                self.invalid = True
                return

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)

    def handle_comment(self, data: str) -> None:
        return


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(get_db_connection()) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
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
        import multipart  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "python-multipart is required for multipart/form-data handling"
        ) from exc
    init_db()


@app.middleware("http")
async def limit_request_resources(request: Request, call_next):
    if not _request_semaphore.acquire(blocking=False):
        return Response(status_code=503)

    try:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_REQUEST_BODY_BYTES:
                    return Response(status_code=400)
            except ValueError:
                return Response(status_code=400)

        return await call_next(request)
    finally:
        _request_semaphore.release()


def is_valid_username(username: str) -> bool:
    if not username:
        return False
    return USERNAME_PATTERN.fullmatch(username) is not None


def validate_username_or_400(username: str) -> str:
    normalized = username.strip()
    if not is_valid_username(normalized):
        raise HTTPException(status_code=400, detail="Invalid username")
    return normalized


def is_png(data: bytes) -> bool:
    png_signature = b"\x89PNG\r\n\x1a\n"
    png_iend = b"IEND\xaeB`\x82"
    return data.startswith(png_signature) and png_iend in data


def is_valid_html_document(value: str) -> bool:
    if not value or not value.strip():
        return False

    lower_value = value.lower()
    if "<" not in value or ">" not in value:
        return False

    parser = BasicHTMLValidator()
    try:
        parser.feed(value)
        parser.close()
    except Exception:
        return False

    if parser.invalid or not parser.seen_tag:
        return False

    if "<html" not in lower_value and "<!doctype html" not in lower_value:
        return False

    return True


async def read_upload_limited(upload: UploadFile, max_bytes: int) -> bytes:
    chunks = []
    total = 0

    while True:
        chunk = await upload.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=400, detail="profile_photo too large")
        chunks.append(chunk)

    return b"".join(chunks)


@app.post("/add_profile", status_code=201, response_class=Response)
async def add_profile(
    username: str = Form(...),
    profile_page: str = Form(...),
    profile_photo: UploadFile = File(...),
):
    username = validate_username_or_400(username)

    profile_page_bytes = profile_page.encode("utf-8")
    if len(profile_page_bytes) > MAX_PROFILE_PAGE_BYTES:
        raise HTTPException(status_code=400, detail="profile_page too large")

    if not is_valid_html_document(profile_page):
        raise HTTPException(status_code=400, detail="Invalid profile_page")

    content_type = (profile_photo.content_type or "").lower()
    if content_type != "image/png":
        raise HTTPException(status_code=400, detail="profile_photo must be a PNG image")

    photo_bytes = await read_upload_limited(profile_photo, MAX_PROFILE_PHOTO_BYTES)
    await profile_photo.close()

    if not photo_bytes:
        raise HTTPException(status_code=400, detail="Empty profile_photo")

    if not is_png(photo_bytes):
        raise HTTPException(status_code=400, detail="profile_photo must be a valid PNG image")

    try:
        with closing(get_db_connection()) as conn:
            conn.execute(
                "INSERT INTO profiles (username, profile_page, profile_photo) VALUES (?, ?, ?)",
                (username, profile_page, photo_bytes),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=403, detail="Profile already exists")

    return Response(status_code=201)


@app.get("/profile/{username}", response_class=HTMLResponse)
def get_profile(username: str):
    username = validate_username_or_400(username)

    with closing(get_db_connection()) as conn:
        row = conn.execute(
            "SELECT profile_page FROM profiles WHERE username = ?",
            (username,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    return HTMLResponse(content=row["profile_page"], status_code=200)


@app.get("/profile-photo/{username}")
def get_profile_photo(username: str):
    username = validate_username_or_400(username)

    with closing(get_db_connection()) as conn:
        row = conn.execute(
            "SELECT profile_photo FROM profiles WHERE username = ?",
            (username,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Profile photo not found")

    return Response(content=row["profile_photo"], media_type="image/png", status_code=200)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)