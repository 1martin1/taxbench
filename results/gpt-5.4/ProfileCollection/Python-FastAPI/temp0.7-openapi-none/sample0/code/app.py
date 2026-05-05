import os
import re
import sqlite3
from contextlib import closing

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
import uvicorn


DB_PATH = "db.sqlite3"

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
    if not isinstance(profile_page, str) or profile_page.strip() == "":
        raise HTTPException(status_code=400, detail="profile_page cannot be empty")
    return profile_page


def validate_png_bytes(data: bytes) -> bytes:
    if not data:
        raise HTTPException(status_code=400, detail="profile_photo cannot be empty")
    png_signature = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(png_signature):
        raise HTTPException(status_code=400, detail="profile_photo must be a PNG file")
    return data


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

    photo_bytes = await profile_photo.read()
    validate_png_bytes(photo_bytes)

    with closing(get_db_connection()) as conn:
        existing = conn.execute(
            "SELECT username FROM profiles WHERE username = ?",
            (username,),
        ).fetchone()
        if existing is not None:
            raise HTTPException(
                status_code=403,
                detail="Profile already exists, creation forbidden",
            )

        conn.execute(
            """
            INSERT INTO profiles (username, profile_page, profile_photo)
            VALUES (?, ?, ?)
            """,
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
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)