import os
import re
import sqlite3
import struct
from contextlib import closing
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, Response
import uvicorn


DB_PATH = "db.sqlite3"
MAX_PROFILE_PAGE_BYTES = 100_000
MAX_PROFILE_PHOTO_BYTES = 5 * 1024 * 1024
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


def is_valid_username(username: str) -> bool:
    if not username:
        return False
    if len(username) > 255:
        return False
    return re.fullmatch(r"[A-Za-z0-9_.-]+", username) is not None


def is_valid_html_document(value: str) -> bool:
    if not value or not value.strip():
        return False
    lowered = value.lower()
    return "<html" in lowered and "</html>" in lowered


def is_valid_png(data: bytes) -> bool:
    if not data or len(data) < len(PNG_SIGNATURE) + 12:
        return False
    if not data.startswith(PNG_SIGNATURE):
        return False

    offset = len(PNG_SIGNATURE)
    seen_ihdr = False
    seen_iend = False

    while offset + 12 <= len(data):
        try:
            length = struct.unpack(">I", data[offset:offset + 4])[0]
        except struct.error:
            return False

        chunk_type = data[offset + 4:offset + 8]
        chunk_data_start = offset + 8
        chunk_data_end = chunk_data_start + length
        crc_end = chunk_data_end + 4

        if crc_end > len(data):
            return False

        if len(chunk_type) != 4 or not all(65 <= b <= 90 or 97 <= b <= 122 for b in chunk_type):
            return False

        if chunk_type == b"IHDR":
            if seen_ihdr or length != 13 or offset != len(PNG_SIGNATURE):
                return False
            seen_ihdr = True
        elif chunk_type == b"IEND":
            if length != 0:
                return False
            seen_iend = True
            if crc_end != len(data):
                return False
            break

        offset = crc_end

    return seen_ihdr and seen_iend


async def parse_multipart_profile_form(request: Request) -> tuple[str, str, bytes]:
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type.lower():
        raise HTTPException(status_code=400, detail="Invalid input")

    try:
        form = await request.form()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid input")

    username_value = form.get("username")
    profile_page_value = form.get("profile_page")
    profile_photo_value = form.get("profile_photo")

    if not isinstance(username_value, str):
        raise HTTPException(status_code=400, detail="Invalid input")
    if not isinstance(profile_page_value, str):
        raise HTTPException(status_code=400, detail="Invalid input")
    if profile_photo_value is None or not hasattr(profile_photo_value, "read"):
        raise HTTPException(status_code=400, detail="Invalid input")

    username = username_value
    profile_page = profile_page_value

    profile_page_bytes = profile_page.encode("utf-8")
    if len(profile_page_bytes) > MAX_PROFILE_PAGE_BYTES:
        raise HTTPException(status_code=400, detail="Invalid input")

    upload = profile_photo_value
    content_type_value = (getattr(upload, "content_type", "") or "").lower()
    if content_type_value != "image/png":
        raise HTTPException(status_code=400, detail="Invalid input")

    try:
        photo_bytes = await upload.read(MAX_PROFILE_PHOTO_BYTES + 1)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        close_method = getattr(upload, "close", None)
        if callable(close_method):
            result = close_method()
            if hasattr(result, "__await__"):
                await result

    if len(photo_bytes) == 0 or len(photo_bytes) > MAX_PROFILE_PHOTO_BYTES:
        raise HTTPException(status_code=400, detail="Invalid input")

    return username, profile_page, photo_bytes


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.post("/add_profile", status_code=201)
async def add_profile(request: Request) -> Response:
    username, profile_page, photo_bytes = await parse_multipart_profile_form(request)

    if not is_valid_username(username):
        raise HTTPException(status_code=400, detail="Invalid input")

    if not is_valid_html_document(profile_page):
        raise HTTPException(status_code=400, detail="Invalid input")

    if not is_valid_png(photo_bytes):
        raise HTTPException(status_code=400, detail="Invalid input")

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
def get_profile(username: str) -> Response:
    if not is_valid_username(username):
        raise HTTPException(status_code=404, detail="Profile not found")

    with closing(get_db_connection()) as conn:
        row = conn.execute(
            "SELECT profile_page FROM profiles WHERE username = ?",
            (username,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    return Response(content=row["profile_page"], media_type="text/html; charset=utf-8", status_code=200)


@app.get("/profile-photo/{username}")
def get_profile_photo(username: str) -> Response:
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


def custom_openapi() -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    paths = openapi_schema.setdefault("paths", {})
    add_profile_path = paths.get("/add_profile", {})
    post_op = add_profile_path.get("post", {})

    post_op["requestBody"] = {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "username": {
                            "type": "string",
                            "description": "The username for the profile",
                        },
                        "profile_page": {
                            "type": "string",
                            "format": "html",
                            "description": "The HTML content for the profile page",
                        },
                        "profile_photo": {
                            "type": "string",
                            "format": "png",
                            "description": "The PNG profile picture",
                        },
                    },
                }
            }
        },
    }

    post_op["responses"] = {
        "201": {"description": "Profile created successfully"},
        "403": {"description": "Profile already exists, creation forbidden"},
        "400": {"description": "Invalid input"},
    }

    profile_get = paths.get("/profile/{username}", {}).get("get", {})
    profile_get["responses"] = {
        "200": {
            "description": "Profile retrieved successfully",
            "content": {
                "text/html": {
                    "schema": {"type": "string"}
                }
            },
        },
        "404": {"description": "Profile not found"},
    }

    photo_get = paths.get("/profile-photo/{username}", {}).get("get", {})
    photo_get["responses"] = {
        "200": {
            "description": "Profile photo retrieved successfully",
            "content": {
                "image/png": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
        },
        "404": {"description": "Profile photo not found"},
    }

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)