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
        raise HTTPException(status_code=400, detail="username too long")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", username):
        raise HTTPException(
            status_code=400,
            detail="username contains invalid characters",
        )
    return username


def validate_html(content: str) -> str:
    if content is None:
        raise HTTPException(status_code=400, detail="profile_page is required")
    if not isinstance(content, str) or content == "":
        raise HTTPException(status_code=400, detail="profile_page cannot be empty")
    return content


async def validate_png(upload: UploadFile) -> bytes:
    if upload is None:
        raise HTTPException(status_code=400, detail="profile_photo is required")

    data = await upload.read()
    if not data:
        raise HTTPException(status_code=400, detail="profile_photo cannot be empty")

    png_signature = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(png_signature):
        raise HTTPException(status_code=400, detail="profile_photo must be a PNG file")

    content_type = upload.content_type or ""
    if content_type and content_type != "image/png":
        raise HTTPException(status_code=400, detail="profile_photo must be image/png")

    return data


@app.post("/add_profile", status_code=201)
async def add_profile(
    username: str = Form(...),
    profile_page: str = Form(...),
    profile_photo: UploadFile = File(...),
):
    validated_username = validate_username(username)
    validated_profile_page = validate_html(profile_page)
    validated_profile_photo = await validate_png(profile_photo)

    with closing(get_db_connection()) as conn:
        existing = conn.execute(
            "SELECT 1 FROM profiles WHERE username = ?",
            (validated_username,),
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
            (
                validated_username,
                validated_profile_page,
                validated_profile_photo,
            ),
        )
        conn.commit()

    return Response(status_code=201)


@app.get("/profile/{username}", response_class=HTMLResponse)
def get_profile(username: str):
    validated_username = validate_username(username)

    with closing(get_db_connection()) as conn:
        row = conn.execute(
            "SELECT profile_page FROM profiles WHERE username = ?",
            (validated_username,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    return HTMLResponse(content=row["profile_page"], status_code=200)


@app.get("/profile-photo/{username}")
def get_profile_photo(username: str):
    validated_username = validate_username(username)

    with closing(get_db_connection()) as conn:
        row = conn.execute(
            "SELECT profile_photo FROM profiles WHERE username = ?",
            (validated_username,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Profile photo not found")

    return Response(content=row["profile_photo"], media_type="image/png", status_code=200)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)