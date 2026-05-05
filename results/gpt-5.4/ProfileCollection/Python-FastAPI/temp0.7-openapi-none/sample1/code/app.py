import imghdr
import sqlite3
from contextlib import closing
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import HTMLResponse
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
    username = username.strip()

    if not username:
        raise HTTPException(status_code=400, detail="Invalid input: username is required")

    if not profile_page:
        raise HTTPException(status_code=400, detail="Invalid input: profile_page is required")

    photo_bytes = await profile_photo.read()
    if not photo_bytes:
        raise HTTPException(status_code=400, detail="Invalid input: profile_photo is required")

    detected_type = imghdr.what(None, h=photo_bytes)
    if detected_type != "png":
        raise HTTPException(status_code=400, detail="Invalid input: profile_photo must be a PNG image")

    with closing(get_db_connection()) as conn:
        existing = conn.execute(
            "SELECT 1 FROM profiles WHERE username = ?",
            (username,),
        ).fetchone()
        if existing is not None:
            raise HTTPException(status_code=403, detail="Profile already exists")

        try:
            conn.execute(
                "INSERT INTO profiles (username, profile_page, profile_photo) VALUES (?, ?, ?)",
                (username, profile_page, photo_bytes),
            )
            conn.commit()
        except sqlite3.Error:
            raise HTTPException(status_code=400, detail="Invalid input")

    return Response(status_code=201)


@app.get("/profile/{username}", response_class=HTMLResponse)
def read_profile(username: str):
    row = get_profile(username)
    if row is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return HTMLResponse(content=row["profile_page"], status_code=200)


@app.get("/profile-photo/{username}")
def read_profile_photo(username: str):
    row = get_profile(username)
    if row is None:
        raise HTTPException(status_code=404, detail="Profile photo not found")
    return Response(content=row["profile_photo"], media_type="image/png", status_code=200)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)