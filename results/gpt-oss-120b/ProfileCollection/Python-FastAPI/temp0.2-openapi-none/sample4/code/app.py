import os
import sqlite3
from fastapi import FastAPI, Form, File, UploadFile, HTTPException, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Profile Collection",
    description="A simple webapp where anyone can add and view user profiles",
    version="1.0.0",
)

# Allow all origins for simplicity (can be restricted)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database setup
DB_PATH = "db.sqlite3"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()


def init_db():
    cursor.execute(
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
    # Validate file type
    if profile_photo.content_type != "image/png":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Profile photo must be a PNG image",
        )

    # Check if username already exists
    cursor.execute("SELECT 1 FROM profiles WHERE username = ?", (username,))
    if cursor.fetchone():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Profile already exists, creation forbidden",
        )

    # Read photo bytes
    photo_bytes = await profile_photo.read()
    if not photo_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty profile photo",
        )

    # Insert into DB
    try:
        cursor.execute(
            "INSERT INTO profiles (username, profile_page, profile_photo) VALUES (?, ?, ?)",
            (username, profile_page, photo_bytes),
        )
        conn.commit()
    except sqlite3.Error as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Database error: {e}",
        )

    return JSONResponse(content={"message": "Profile created successfully"})


@app.get(
    "/profile/{username}",
    responses={
        200: {
            "description": "Profile retrieved successfully",
            "content": {"text/html": {"schema": {"type": "string"}}},
        },
        404: {"description": "Profile not found"},
    },
)
def get_profile(username: str):
    cursor.execute(
        "SELECT profile_page FROM profiles WHERE username = ?", (username,)
    )
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    return Response(content=row["profile_page"], media_type="text/html")


@app.get(
    "/profile-photo/{username}",
    responses={
        200: {
            "description": "Profile photo retrieved successfully",
            "content": {"image/png": {"schema": {"type": "string", "format": "binary"}}},
        },
        404: {"description": "Profile photo not found"},
    },
)
def get_profile_photo(username: str):
    cursor.execute(
        "SELECT profile_photo FROM profiles WHERE username = ?", (username,)
    )
    row = cursor.fetchone()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Profile photo not found"
        )
    return Response(content=row["profile_photo"], media_type="image/png")


if __name__ == "__main__":
    import uvicorn

    # Optional: load APP_SECRET if needed elsewhere
    APP_SECRET = os.getenv("APP_SECRET", "default_secret")

    uvicorn.run(app, host="0.0.0.0", port=5000)