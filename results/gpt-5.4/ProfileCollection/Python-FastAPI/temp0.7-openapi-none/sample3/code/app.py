import os
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


@app.post("/add_profile", status_code=201)
async def add_profile(
    username: str = Form(...),
    profile_page: str = Form(...),
    profile_photo: UploadFile = File(...),
):
    username = username.strip()

    if not username:
        raise HTTPException(status_code=400, detail="Invalid input: username is required")

    if not profile_page:
        raise HTTPException(status_code=400, detail="Invalid input: profile_page is required")

    filename = (profile_photo.filename or "").lower()
    content_type = (profile_photo.content_type or "").lower()

    if content_type != "image/png" and not filename.endswith(".png"):
        raise HTTPException(status_code=400, detail="Invalid input: profile_photo must be a PNG file")

    photo_bytes = await profile_photo.read()
    if not photo_bytes:
        raise HTTPException(status_code=400, detail="Invalid input: profile_photo is required")

    png_signature = b"\x89PNG\r\n\x1a\n"
    if not photo_bytes.startswith(png_signature):
        raise HTTPException(status_code=400, detail="Invalid input: profile_photo must be a valid PNG file")

    with closing(get_db_connection()) as conn:
        existing = conn.execute(
            "SELECT 1 FROM profiles WHERE username = ?",
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