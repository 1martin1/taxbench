import os
import sqlite3

from fastapi import FastAPI, Form, File, UploadFile, HTTPException, status
from fastapi.responses import Response
import uvicorn

app = FastAPI()

DB_PATH = "db.sqlite3"


def init_db() -> None:
    """Create the profiles table if it does not exist."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS profiles (
            username TEXT PRIMARY KEY,
            profile_page TEXT NOT NULL,
            profile_photo BLOB NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


@app.on_event("startup")
def on_startup():
    init_db()


@app.post(
    "/add_profile",
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Profile created successfully"},
        403: {"description": "Profile already exists, creation forbidden"},
        400: {"description": "Invalid input"},
    },
)
async def add_profile(
    username: str = Form(...),
    profile_page: str = Form(...),
    profile_photo: UploadFile = File(...),
):
    """
    Add a new user profile.
    Expects multipart/form-data with fields:
    - username (string)
    - profile_page (HTML string)
    - profile_photo (PNG file)
    """
    # Validate that the uploaded file is a PNG image
    if profile_photo.content_type != "image/png":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Profile photo must be a PNG image",
        )

    # Read the binary content of the uploaded PNG
    photo_bytes = await profile_photo.read()

    # Insert into the database, handling duplicate usernames
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO profiles (username, profile_page, profile_photo) VALUES (?, ?, ?)",
            (username, profile_page, photo_bytes),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Profile already exists",
        )
    finally:
        conn.close()

    return {"message": "Profile created successfully"}


@app.get(
    "/profile/{username}",
    responses={
        200: {"description": "Profile retrieved successfully", "content": {"text/html": {}}},
        404: {"description": "Profile not found"},
    },
)
def get_profile(username: str):
    """
    Retrieve the HTML profile page for a given username.
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT profile_page FROM profiles WHERE username = ?",
        (username,),
    )
    row = cur.fetchone()
    conn.close()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found",
        )

    html_content = row[0]
    return Response(content=html_content, media_type="text/html")


@app.get(
    "/profile-photo/{username}",
    responses={
        200: {"description": "Profile photo retrieved successfully", "content": {"image/png": {}}},
        404: {"description": "Profile photo not found"},
    },
)
def get_profile_photo(username: str):
    """
    Retrieve the PNG profile photo for a given username.
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT profile_photo FROM profiles WHERE username = ?",
        (username,),
    )
    row = cur.fetchone()
    conn.close()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile photo not found",
        )

    photo_bytes = row[0]
    return Response(content=photo_bytes, media_type="image/png")


if __name__ == "__main__":
    # The APP_SECRET environment variable is available if needed elsewhere.
    _ = os.getenv("APP_SECRET")
    uvicorn.run(app, host="0.0.0.0", port=5000)