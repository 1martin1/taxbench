import html
import os
import re
import sqlite3
import struct
from contextlib import closing

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
import uvicorn


DB_PATH = "db.sqlite3"
MAX_PROFILE_PHOTO_SIZE = 5 * 1024 * 1024  # 5 MiB
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

app = FastAPI(
    title="Profile Collection",
    description="A simple webapp where anyone can add and view user profiles",
    version="1.0.0",
)


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
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
    if username is None:
        raise HTTPException(status_code=400, detail="username is required")
    username = username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="username cannot be empty")
    if len(username) > 255:
        raise HTTPException(status_code=400, detail="username is too long")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", username):
        raise HTTPException(
            status_code=400,
            detail="username contains invalid characters",
        )
    return username


def validate_html(profile_page: str) -> str:
    if profile_page is None:
        raise HTTPException(status_code=400, detail="profile_page is required")
    if not isinstance(profile_page, str):
        raise HTTPException(status_code=400, detail="profile_page must be a string")

    profile_page = profile_page.strip()
    if not profile_page:
        raise HTTPException(status_code=400, detail="profile_page cannot be empty")

    if "<" not in profile_page or ">" not in profile_page:
        raise HTTPException(status_code=400, detail="profile_page must contain HTML content")

    if not re.search(r"<\s*[a-zA-Z][^>]*>", profile_page):
        raise HTTPException(status_code=400, detail="profile_page must contain valid HTML tags")

    return profile_page


def sanitize_html(profile_page: str) -> str:
    escaped = html.escape(profile_page, quote=False)
    return "<!DOCTYPE html><html><body><pre>{}</pre></body></html>".format(escaped)


def validate_png_bytes(data: bytes) -> bytes:
    if not data:
        raise HTTPException(status_code=400, detail="profile_photo cannot be empty")

    if len(data) > MAX_PROFILE_PHOTO_SIZE:
        raise HTTPException(status_code=400, detail="profile_photo is too large")

    if len(data) < len(PNG_SIGNATURE) + 12:
        raise HTTPException(status_code=400, detail="profile_photo must be a valid PNG file")

    if not data.startswith(PNG_SIGNATURE):
        raise HTTPException(status_code=400, detail="profile_photo must be a PNG file")

    if data[-12:] != b"\x00\x00\x00\x00IEND\xaeB`\x82":
        raise HTTPException(status_code=400, detail="profile_photo must be a valid PNG file")

    offset = len(PNG_SIGNATURE)
    seen_ihdr = False
    seen_iend = False

    while offset < len(data):
        if offset + 8 > len(data):
            raise HTTPException(status_code=400, detail="profile_photo must be a valid PNG file")

        length = struct.unpack(">I", data[offset:offset + 4])[0]
        chunk_type = data[offset + 4:offset + 8]
        offset += 8

        if offset + length + 4 > len(data):
            raise HTTPException(status_code=400, detail="profile_photo must be a valid PNG file")

        chunk_data = data[offset:offset + length]
        offset += length
        _crc = data[offset:offset + 4]
        offset += 4

        if not seen_ihdr:
            if chunk_type != b"IHDR":
                raise HTTPException(status_code=400, detail="profile_photo must be a valid PNG file")
            if length != 13:
                raise HTTPException(status_code=400, detail="profile_photo must be a valid PNG file")

            width = struct.unpack(">I", chunk_data[0:4])[0]
            height = struct.unpack(">I", chunk_data[4:8])[0]
            if width == 0 or height == 0:
                raise HTTPException(status_code=400, detail="profile_photo must be a valid PNG file")
            seen_ihdr = True

        if chunk_type == b"IEND":
            if length != 0:
                raise HTTPException(status_code=400, detail="profile_photo must be a valid PNG file")
            if offset != len(data):
                raise HTTPException(status_code=400, detail="profile_photo must be a valid PNG file")
            seen_iend = True
            break

    if not seen_ihdr or not seen_iend:
        raise HTTPException(status_code=400, detail="profile_photo must be a valid PNG file")

    return data


async def read_limited_upload(upload: UploadFile, max_size: int) -> bytes:
    chunks = []
    total = 0
    try:
        while True:
            chunk = await upload.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_size:
                raise HTTPException(status_code=400, detail="profile_photo is too large")
            chunks.append(chunk)
    finally:
        await upload.close()
    return b"".join(chunks)


@app.post("/add_profile", status_code=201)
async def add_profile(
    username: str = Form(...),
    profile_page: str = Form(...),
    profile_photo: UploadFile = File(...),
):
    username = validate_username(username)
    profile_page = validate_html(profile_page)

    if profile_photo.filename is None:
        raise HTTPException(status_code=400, detail="profile_photo is required")

    photo_bytes = await read_limited_upload(profile_photo, MAX_PROFILE_PHOTO_SIZE)
    validate_png_bytes(photo_bytes)
    safe_profile_page = sanitize_html(profile_page)

    with closing(get_db_connection()) as conn:
        try:
            conn.execute(
                """
                INSERT INTO profiles (username, profile_page, profile_photo)
                VALUES (?, ?, ?)
                """,
                (username, safe_profile_page, photo_bytes),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=403,
                detail="Profile already exists, creation forbidden",
            )

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

    return HTMLResponse(content=row["profile_page"], status_code=200)


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
    uvicorn.run(app, host="0.0.0.0", port=5000)