import html
import re
import sqlite3
from contextlib import closing
from typing import Iterator

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from starlette.requests import Request
from starlette.routing import BaseRoute
from contextlib import asynccontextmanager


DB_PATH = "db.sqlite3"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_\-\.]+")


def is_valid_username(username: str) -> bool:
    if not username:
        return False
    return USERNAME_PATTERN.fullmatch(username) is not None


def sanitize_profile_html(content: str) -> str:
    return html.escape(content, quote=True)


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


@asynccontextmanager
async def lifespan(app: FastAPI) -> Iterator[None]:
    init_db()
    yield


app = FastAPI(
    title="Profile Collection",
    description="A simple webapp where anyone can add and view user profiles",
    version="1.0.0",
    lifespan=lifespan,
)


@app.post("/add_profile", status_code=201, response_class=Response)
async def add_profile(
    username: str = Form(...),
    profile_page: str = Form(...),
    profile_photo: UploadFile = File(...),
):
    if not is_valid_username(username):
        raise HTTPException(status_code=400, detail="Invalid username")

    if not profile_page.strip():
        raise HTTPException(status_code=400, detail="profile_page must not be empty")

    content_type = (profile_photo.content_type or "").lower()
    if content_type != "image/png":
        raise HTTPException(status_code=400, detail="profile_photo must be a PNG image")

    photo_bytes = await profile_photo.read()
    await profile_photo.close()

    if not photo_bytes:
        raise HTTPException(status_code=400, detail="profile_photo must not be empty")

    if not photo_bytes.startswith(PNG_SIGNATURE):
        raise HTTPException(status_code=400, detail="profile_photo must be a valid PNG image")

    safe_profile_page = sanitize_profile_html(profile_page)

    with closing(get_db_connection()) as conn:
        existing = conn.execute(
            "SELECT username FROM profiles WHERE username = ?",
            (username,),
        ).fetchone()
        if existing is not None:
            raise HTTPException(status_code=403, detail="Profile already exists")

        conn.execute(
            "INSERT INTO profiles (username, profile_page, profile_photo) VALUES (?, ?, ?)",
            (username, safe_profile_page, photo_bytes),
        )
        conn.commit()

    return Response(status_code=201)


@app.get("/profile/{username}", response_class=HTMLResponse)
def get_profile(username: str):
    if not is_valid_username(username):
        raise HTTPException(status_code=404, detail="Profile not found")

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
    if not is_valid_username(username):
        raise HTTPException(status_code=404, detail="Profile photo not found")

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