import os
import re
import sqlite3
from contextlib import closing
from html import escape
from html.parser import HTMLParser

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
import uvicorn


DB_PATH = "db.sqlite3"
MAX_USERNAME_LENGTH = 255
MAX_PROFILE_PAGE_LENGTH = 1_000_000
MAX_PROFILE_PHOTO_SIZE = 10 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_IEND = b"IEND"


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


class SafeHTMLValidator(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.seen_tag = False
        self.disallowed_found = False
        self.disallowed_attrs_found = False

    def handle_starttag(self, tag: str, attrs) -> None:
        self.seen_tag = True
        lower_tag = tag.lower()
        if lower_tag in {"script", "iframe", "object", "embed", "link", "meta", "style", "base"}:
            self.disallowed_found = True
            return

        for attr_name, attr_value in attrs:
            attr_name_lower = (attr_name or "").lower()
            attr_value_lower = (attr_value or "").strip().lower()

            if attr_name_lower.startswith("on"):
                self.disallowed_attrs_found = True
                return

            if attr_name_lower in {"src", "href", "xlink:href", "formaction"} and (
                attr_value_lower.startswith("javascript:")
                or attr_value_lower.startswith("data:")
            ):
                self.disallowed_attrs_found = True
                return

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)


def validate_username(username: str) -> str:
    if username is None:
        raise HTTPException(status_code=400, detail="username is required")
    username = username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="username cannot be empty")
    if len(username) > MAX_USERNAME_LENGTH:
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
    if not isinstance(content, str):
        raise HTTPException(status_code=400, detail="profile_page must be a string")

    normalized = content.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="profile_page cannot be empty")
    if len(content) > MAX_PROFILE_PAGE_LENGTH:
        raise HTTPException(status_code=400, detail="profile_page too large")

    parser = SafeHTMLValidator()
    try:
        parser.feed(content)
        parser.close()
    except Exception:
        raise HTTPException(status_code=400, detail="profile_page must be valid HTML")

    if not parser.seen_tag:
        raise HTTPException(status_code=400, detail="profile_page must be valid HTML")
    if parser.disallowed_found or parser.disallowed_attrs_found:
        raise HTTPException(status_code=400, detail="profile_page contains unsafe HTML")

    return content


def validate_png_bytes(data: bytes) -> None:
    if not data.startswith(PNG_SIGNATURE):
        raise HTTPException(status_code=400, detail="profile_photo must be a PNG file")
    if len(data) < len(PNG_SIGNATURE) + 12:
        raise HTTPException(status_code=400, detail="profile_photo must be a valid PNG file")
    if PNG_IEND not in data:
        raise HTTPException(status_code=400, detail="profile_photo must be a valid PNG file")


async def validate_png(upload: UploadFile) -> bytes:
    if upload is None:
        raise HTTPException(status_code=400, detail="profile_photo is required")

    data = await upload.read()
    await upload.close()

    if not data:
        raise HTTPException(status_code=400, detail="profile_photo cannot be empty")
    if len(data) > MAX_PROFILE_PHOTO_SIZE:
        raise HTTPException(status_code=400, detail="profile_photo too large")

    content_type = (upload.content_type or "").strip().lower()
    if content_type and content_type != "image/png":
        raise HTTPException(status_code=400, detail="profile_photo must be image/png")

    validate_png_bytes(data)
    return data


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.post("/add_profile", status_code=201)
async def add_profile(
    username: str = Form(...),
    profile_page: str = Form(...),
    profile_photo: UploadFile = File(...),
):
    validated_username = validate_username(username)
    validated_profile_page = validate_html(profile_page)
    validated_profile_photo = await validate_png(profile_photo)

    try:
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
    except HTTPException:
        raise
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=403,
            detail="Profile already exists, creation forbidden",
        )
    except sqlite3.Error:
        raise HTTPException(status_code=400, detail="Invalid input")

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

    safe_html = (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\"></head><body><pre>"
        + escape(row["profile_page"])
        + "</pre></body></html>"
    )
    return HTMLResponse(content=safe_html, status_code=200)


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