import os
import re
import sqlite3
from contextlib import closing
from html.parser import HTMLParser
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
import uvicorn

DB_PATH = "db.sqlite3"

MAX_USERNAME_LENGTH = 64
MAX_PROFILE_PAGE_LENGTH = 100_000
MAX_PROFILE_PHOTO_BYTES = 5 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class SimpleHTMLValidator(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.has_meaningful_content = False

    def handle_starttag(self, tag: str, attrs) -> None:
        self.has_meaningful_content = True

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.has_meaningful_content = True

    def handle_data(self, data: str) -> None:
        if data and data.strip():
            self.has_meaningful_content = True


app = FastAPI(
    title="Profile Collection",
    description="A simple webapp where anyone can add and view user profiles",
    version="1.0.0",
)


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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


def validate_username(username: str) -> str:
    username = username.strip()
    if not username or len(username) > MAX_USERNAME_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid input")
    return username


def validate_profile_page(profile_page: str) -> str:
    if not profile_page or len(profile_page) > MAX_PROFILE_PAGE_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid input")

    parser = SimpleHTMLValidator()
    try:
        parser.feed(profile_page)
        parser.close()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid input")

    if not parser.has_meaningful_content:
        raise HTTPException(status_code=400, detail="Invalid input")

    return profile_page


def sanitize_html_for_serving(profile_page: str) -> str:
    sanitized = re.sub(r"(?is)<script\b[^>]*>.*?</script>", "", profile_page)
    sanitized = re.sub(r"(?i)\son\w+\s*=\s*(\".*?\"|'.*?'|[^\s>]+)", "", sanitized)
    sanitized = re.sub(r"(?i)\s(href|src)\s*=\s*(['\"])\s*javascript:.*?\2", "", sanitized)
    sanitized = re.sub(r"(?i)\s(href|src)\s*=\s*javascript:[^\s>]+", "", sanitized)
    return sanitized


async def read_and_validate_png(upload: UploadFile) -> bytes:
    if upload is None:
        raise HTTPException(status_code=400, detail="Invalid input")

    total_size = 0
    chunks = []
    first_chunk: Optional[bytes] = None

    try:
        while True:
            chunk = await upload.read(8192)
            if not chunk:
                break
            if first_chunk is None:
                first_chunk = chunk
            total_size += len(chunk)
            if total_size > MAX_PROFILE_PHOTO_BYTES:
                raise HTTPException(status_code=400, detail="Invalid input")
            chunks.append(chunk)
    finally:
        await upload.close()

    if total_size == 0:
        raise HTTPException(status_code=400, detail="Invalid input")

    photo_bytes = b"".join(chunks)

    if len(photo_bytes) < len(PNG_SIGNATURE) or not photo_bytes.startswith(PNG_SIGNATURE):
        raise HTTPException(status_code=400, detail="Invalid input")

    return photo_bytes


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.post("/add_profile", status_code=201, response_class=Response)
async def add_profile(
    username: str = Form(...),
    profile_page: str = Form(...),
    profile_photo: UploadFile = File(...),
):
    username = validate_username(username)
    profile_page = validate_profile_page(profile_page)
    photo_bytes = await read_and_validate_png(profile_photo)

    with closing(get_db_connection()) as conn:
        existing = conn.execute(
            "SELECT username FROM profiles WHERE username = ?",
            (username,),
        ).fetchone()

        if existing is not None:
            raise HTTPException(status_code=403, detail="Profile already exists")

        conn.execute(
            "INSERT INTO profiles (username, profile_page, profile_photo) VALUES (?, ?, ?)",
            (username, profile_page, photo_bytes),
        )
        conn.commit()

    return Response(status_code=201)


@app.get("/profile/{username}", response_class=HTMLResponse)
def get_profile(username: str):
    username = validate_username(username)

    with closing(get_db_connection()) as conn:
        row = conn.execute(
            "SELECT profile_page FROM profiles WHERE username = ?",
            (username,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    safe_html = sanitize_html_for_serving(row["profile_page"])
    return HTMLResponse(content=safe_html, status_code=200)


@app.get("/profile-photo/{username}")
def get_profile_photo(username: str):
    username = validate_username(username)

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