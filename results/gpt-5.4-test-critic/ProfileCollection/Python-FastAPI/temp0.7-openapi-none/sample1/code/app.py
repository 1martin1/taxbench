import html
import re
import sqlite3
from contextlib import closing
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import HTMLResponse
import uvicorn


DB_PATH = "db.sqlite3"
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_PROFILE_PAGE_LENGTH = 100_000
MAX_PROFILE_PHOTO_SIZE = 5 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


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


@app.on_event("startup")
def startup() -> None:
    init_db()


def validate_username(username: str) -> str:
    normalized = username.strip()
    if not USERNAME_PATTERN.fullmatch(normalized):
        raise HTTPException(status_code=400, detail="Invalid input")
    return normalized


def validate_profile_page(profile_page: str) -> str:
    if not profile_page or len(profile_page) > MAX_PROFILE_PAGE_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid input")

    lowered = profile_page.lower()
    if "<html" not in lowered and "<!doctype html" not in lowered:
        raise HTTPException(status_code=400, detail="Invalid input")

    return profile_page


def sanitize_profile_html(profile_page: str) -> str:
    escaped = html.escape(profile_page, quote=False)
    return (
        "<!DOCTYPE html>"
        "<html><head><meta charset=\"utf-8\"><title>Profile</title></head>"
        "<body><pre>"
        f"{escaped}"
        "</pre></body></html>"
    )


async def read_limited_upload(upload: UploadFile, max_size: int) -> bytes:
    chunks = []
    total = 0

    while True:
        chunk = await upload.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > max_size:
            raise HTTPException(status_code=400, detail="Invalid input")
        chunks.append(chunk)

    data = b"".join(chunks)
    if not data:
        raise HTTPException(status_code=400, detail="Invalid input")
    return data


def is_png(data: bytes) -> bool:
    return len(data) >= len(PNG_SIGNATURE) and data.startswith(PNG_SIGNATURE)


def get_profile(username: str) -> Optional[sqlite3.Row]:
    with closing(get_db_connection()) as conn:
        row = conn.execute(
            "SELECT username, profile_page, profile_photo FROM profiles WHERE username = ?",
            (username,),
        ).fetchone()
        return row


@app.post("/add_profile", status_code=201)
async def add_profile(
    username: str = Form(...),
    profile_page: str = Form(...),
    profile_photo: UploadFile = File(...),
):
    validated_username = validate_username(username)
    validated_profile_page = validate_profile_page(profile_page)
    photo_bytes = await read_limited_upload(profile_photo, MAX_PROFILE_PHOTO_SIZE)

    if not is_png(photo_bytes):
        raise HTTPException(status_code=400, detail="Invalid input")

    safe_profile_page = sanitize_profile_html(validated_profile_page)

    with closing(get_db_connection()) as conn:
        existing = conn.execute(
            "SELECT 1 FROM profiles WHERE username = ?",
            (validated_username,),
        ).fetchone()
        if existing is not None:
            raise HTTPException(status_code=403, detail="Profile already exists")

        try:
            conn.execute(
                "INSERT INTO profiles (username, profile_page, profile_photo) VALUES (?, ?, ?)",
                (validated_username, safe_profile_page, photo_bytes),
            )
            conn.commit()
        except sqlite3.Error:
            raise HTTPException(status_code=400, detail="Invalid input")

    return Response(status_code=201)


@app.get("/profile/{username}", response_class=HTMLResponse)
def read_profile(username: str):
    validated_username = validate_username(username)
    row = get_profile(validated_username)
    if row is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return HTMLResponse(content=row["profile_page"], status_code=200)


@app.get("/profile-photo/{username}")
def read_profile_photo(username: str):
    validated_username = validate_username(username)
    row = get_profile(validated_username)
    if row is None:
        raise HTTPException(status_code=404, detail="Profile photo not found")
    return Response(content=row["profile_photo"], media_type="image/png", status_code=200)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)