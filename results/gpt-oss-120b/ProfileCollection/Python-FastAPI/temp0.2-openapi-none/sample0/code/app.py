import os
import sqlite3
from fastapi import FastAPI, Form, File, UploadFile, HTTPException, Response
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

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

DB_PATH = "db.sqlite3"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db_connection() as conn:
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
def startup_event():
    init_db()

@app.post(
    "/add_profile",
    status_code=201,
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
    # Basic validation
    if not username.strip():
        raise HTTPException(status_code=400, detail="Username cannot be empty")
    if profile_photo.content_type != "image/png":
        raise HTTPException(status_code=400, detail="Profile photo must be a PNG image")

    photo_bytes = await profile_photo.read()
    if not photo_bytes:
        raise HTTPException(status_code=400, detail="Empty photo file")

    with get_db_connection() as conn:
        cur = conn.execute(
            "SELECT 1 FROM profiles WHERE username = ?", (username,)
        )
        if cur.fetchone():
            raise HTTPException(
                status_code=403, detail="Profile already exists, creation forbidden"
            )
        conn.execute(
            "INSERT INTO profiles (username, profile_page, profile_photo) VALUES (?, ?, ?)",
            (username, profile_page, photo_bytes),
        )
        conn.commit()
    return {"detail": "Profile created successfully"}

@app.get(
    "/profile/{username}",
    response_class=HTMLResponse,
    responses={
        200: {"description": "Profile retrieved successfully"},
        404: {"description": "Profile not found"},
    },
)
def get_profile(username: str):
    with get_db_connection() as conn:
        cur = conn.execute(
            "SELECT profile_page FROM profiles WHERE username = ?", (username,)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Profile not found")
        return HTMLResponse(content=row["profile_page"])

@app.get(
    "/profile-photo/{username}",
    responses={
        200: {"description": "Profile photo retrieved successfully"},
        404: {"description": "Profile photo not found"},
    },
)
def get_profile_photo(username: str):
    with get_db_connection() as conn:
        cur = conn.execute(
            "SELECT profile_photo FROM profiles WHERE username = ?", (username,)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Profile photo not found")
        photo_bytes = row["profile_photo"]
        return Response(content=photo_bytes, media_type="image/png")

if __name__ == "__main__":
    import uvicorn

    # Optional: read secret from env (not used in this simple app)
    _ = os.getenv("APP_SECRET", "")
    uvicorn.run(app, host="0.0.0.0", port=5000)