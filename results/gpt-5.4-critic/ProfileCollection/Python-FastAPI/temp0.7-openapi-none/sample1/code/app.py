import html
import os
import re
import sqlite3
import struct
import time
from collections import defaultdict, deque
from contextlib import closing
from typing import Deque, Dict, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
import uvicorn


DB_PATH = "db.sqlite3"

MAX_USERNAME_LENGTH = 64
MAX_PROFILE_PAGE_LENGTH = 100_000
MAX_PROFILE_PHOTO_SIZE = 5 * 1024 * 1024

RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_ADD_PROFILE = 20
RATE_LIMIT_MAX_READS = 120

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


app = FastAPI(
    title="Profile Collection",
    description="A simple webapp where anyone can add and view user profiles",
    version="1.0.0",
)


_rate_limit_store: Dict[str, Deque[float]] = defaultdict(deque)


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
    if not username or len(username) > MAX_USERNAME_LENGTH:
        return False
    return re.fullmatch(r"[A-Za-z0-9_.-]+", username) is not None


def get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        first_ip = forwarded_for.split(",")[0].strip()
        if first_ip:
            return first_ip
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def enforce_rate_limit(request: Request, bucket: str, max_requests: int) -> None:
    client_ip = get_client_ip(request)
    key = f"{bucket}:{client_ip}"
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW_SECONDS
    entries = _rate_limit_store[key]

    while entries and entries[0] < window_start:
        entries.popleft()

    if len(entries) >= max_requests:
        raise HTTPException(status_code=429, detail="Too many requests")

    entries.append(now)


def validate_html_document(profile_page: str) -> bool:
    if not profile_page or not profile_page.strip():
        return False
    if len(profile_page) > MAX_PROFILE_PAGE_LENGTH:
        return False

    lowered = profile_page.lower()
    html_like_patterns = ("<html", "<body", "<div", "<p", "<span", "<h1", "<h2", "<h3", "<!doctype html")
    if not any(pattern in lowered for pattern in html_like_patterns):
        return False

    if "<" not in profile_page or ">" not in profile_page:
        return False

    return True


def sanitize_html(profile_page: str) -> str:
    escaped = html.escape(profile_page, quote=False)
    return (
        "<!DOCTYPE html>"
        "<html><head><meta charset=\"utf-8\"><title>Profile</title></head>"
        "<body><pre>"
        f"{escaped}"
        "</pre></body></html>"
    )


def parse_multipart_boundary(content_type: str) -> Optional[bytes]:
    if not content_type:
        return None

    parts = [part.strip() for part in content_type.split(";")]
    if not parts or parts[0].lower() != "multipart/form-data":
        return None

    for part in parts[1:]:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key.strip().lower() == "boundary":
            boundary = value.strip()
            if len(boundary) >= 2 and boundary[0] == boundary[-1] == '"':
                boundary = boundary[1:-1]
            if not boundary:
                return None
            return boundary.encode("utf-8", errors="strict")
    return None


def parse_headers_block(header_bytes: bytes) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    header_text = header_bytes.decode("utf-8", errors="replace")
    for line in header_text.split("\r\n"):
        if not line or ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    return headers


def parse_content_disposition(value: str) -> Tuple[Optional[str], Optional[str]]:
    disposition_type = None
    field_name = None
    filename = None

    parts = [part.strip() for part in value.split(";")]
    if parts:
        disposition_type = parts[0].lower()

    for part in parts[1:]:
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        key = key.strip().lower()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] == '"':
            val = val[1:-1]
        if key == "name":
            field_name = val
        elif key == "filename":
            filename = val

    if disposition_type != "form-data":
        return None, None

    return field_name, filename


def parse_multipart_form_data(body: bytes, content_type: str) -> Dict[str, Tuple[Dict[str, str], bytes]]:
    boundary = parse_multipart_boundary(content_type)
    if boundary is None:
        raise HTTPException(status_code=400, detail="Invalid multipart/form-data")

    delimiter = b"--" + boundary
    if delimiter not in body:
        raise HTTPException(status_code=400, detail="Invalid multipart/form-data")

    result: Dict[str, Tuple[Dict[str, str], bytes]] = {}

    segments = body.split(delimiter)
    for segment in segments[1:]:
        if segment in (b"--", b"--\r\n", b"", b"\r\n"):
            continue

        if segment.startswith(b"\r\n"):
            segment = segment[2:]

        if segment.endswith(b"--\r\n"):
            segment = segment[:-4]
        elif segment.endswith(b"--"):
            segment = segment[:-2]
        elif segment.endswith(b"\r\n"):
            segment = segment[:-2]

        header_end = segment.find(b"\r\n\r\n")
        if header_end == -1:
            raise HTTPException(status_code=400, detail="Invalid multipart/form-data")

        header_bytes = segment[:header_end]
        content = segment[header_end + 4 :]

        headers = parse_headers_block(header_bytes)
        content_disposition = headers.get("content-disposition")
        if not content_disposition:
            raise HTTPException(status_code=400, detail="Invalid multipart/form-data")

        field_name, _filename = parse_content_disposition(content_disposition)
        if not field_name:
            raise HTTPException(status_code=400, detail="Invalid multipart/form-data")

        result[field_name] = (headers, content)

    return result


def decode_form_text(value: bytes, field_name: str, max_length: int) -> str:
    if len(value) > max_length:
        raise HTTPException(status_code=400, detail=f"{field_name} too large")
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}")


def validate_png(photo_bytes: bytes) -> bool:
    if not photo_bytes:
        return False
    if len(photo_bytes) > MAX_PROFILE_PHOTO_SIZE:
        return False
    if not photo_bytes.startswith(PNG_SIGNATURE):
        return False

    offset = len(PNG_SIGNATURE)
    seen_ihdr = False
    seen_iend = False

    try:
        while offset < len(photo_bytes):
            if offset + 8 > len(photo_bytes):
                return False

            length = struct.unpack(">I", photo_bytes[offset : offset + 4])[0]
            chunk_type = photo_bytes[offset + 4 : offset + 8]
            offset += 8

            if offset + length + 4 > len(photo_bytes):
                return False

            chunk_data = photo_bytes[offset : offset + length]
            _crc = photo_bytes[offset + length : offset + length + 4]
            offset += length + 4

            if len(chunk_type) != 4 or not all(65 <= b <= 90 or 97 <= b <= 122 for b in chunk_type):
                return False

            if chunk_type == b"IHDR":
                if seen_ihdr or length != 13:
                    return False
                width = struct.unpack(">I", chunk_data[0:4])[0]
                height = struct.unpack(">I", chunk_data[4:8])[0]
                bit_depth = chunk_data[8]
                color_type = chunk_data[9]
                compression_method = chunk_data[10]
                filter_method = chunk_data[11]
                interlace_method = chunk_data[12]

                if width == 0 or height == 0:
                    return False
                if compression_method != 0 or filter_method != 0 or interlace_method not in (0, 1):
                    return False
                valid_bit_depths = {
                    0: {1, 2, 4, 8, 16},
                    2: {8, 16},
                    3: {1, 2, 4, 8},
                    4: {8, 16},
                    6: {8, 16},
                }
                if color_type not in valid_bit_depths or bit_depth not in valid_bit_depths[color_type]:
                    return False
                seen_ihdr = True

            elif chunk_type == b"IEND":
                if length != 0:
                    return False
                seen_iend = True
                if offset != len(photo_bytes):
                    return False
                break

        return seen_ihdr and seen_iend
    except (IndexError, struct.error):
        return False


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.post("/add_profile", status_code=201)
async def add_profile(request: Request):
    enforce_rate_limit(request, "add_profile", RATE_LIMIT_MAX_ADD_PROFILE)

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > (MAX_PROFILE_PAGE_LENGTH + MAX_PROFILE_PHOTO_SIZE + 100_000):
                raise HTTPException(status_code=400, detail="Request too large")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")

    body = await request.body()
    if len(body) > (MAX_PROFILE_PAGE_LENGTH + MAX_PROFILE_PHOTO_SIZE + 100_000):
        raise HTTPException(status_code=400, detail="Request too large")

    form_parts = parse_multipart_form_data(body, request.headers.get("content-type", ""))

    if "username" not in form_parts or "profile_page" not in form_parts or "profile_photo" not in form_parts:
        raise HTTPException(status_code=400, detail="Invalid input")

    username = decode_form_text(form_parts["username"][1], "username", MAX_USERNAME_LENGTH)
    profile_page = decode_form_text(form_parts["profile_page"][1], "profile_page", MAX_PROFILE_PAGE_LENGTH)
    photo_headers, photo_bytes = form_parts["profile_photo"]

    if not is_valid_username(username):
        raise HTTPException(status_code=400, detail="Invalid username")

    if not validate_html_document(profile_page):
        raise HTTPException(status_code=400, detail="Invalid profile_page")

    photo_content_type = (photo_headers.get("content-type") or "").lower()
    if photo_content_type != "image/png":
        raise HTTPException(status_code=400, detail="profile_photo must be a PNG image")

    if not validate_png(photo_bytes):
        raise HTTPException(status_code=400, detail="profile_photo must be a valid PNG image")

    safe_profile_page = sanitize_html(profile_page)

    try:
        with closing(get_db_connection()) as conn:
            existing = conn.execute(
                "SELECT 1 FROM profiles WHERE username = ?",
                (username,),
            ).fetchone()
            if existing is not None:
                raise HTTPException(status_code=403, detail="Profile already exists")

            conn.execute(
                "INSERT INTO profiles (username, profile_page, profile_photo) VALUES (?, ?, ?)",
                (username, safe_profile_page, photo_bytes),
            )
            conn.commit()
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Database error")

    return Response(status_code=201)


@app.get("/profile/{username}", response_class=HTMLResponse)
def get_profile(username: str, request: Request):
    enforce_rate_limit(request, "get_profile", RATE_LIMIT_MAX_READS)

    if not is_valid_username(username):
        raise HTTPException(status_code=404, detail="Profile not found")

    try:
        with closing(get_db_connection()) as conn:
            row = conn.execute(
                "SELECT profile_page FROM profiles WHERE username = ?",
                (username,),
            ).fetchone()
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Database error")

    if row is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    return HTMLResponse(content=row["profile_page"], status_code=200)


@app.get("/profile-photo/{username}")
def get_profile_photo(username: str, request: Request):
    enforce_rate_limit(request, "get_profile_photo", RATE_LIMIT_MAX_READS)

    if not is_valid_username(username):
        raise HTTPException(status_code=404, detail="Profile photo not found")

    try:
        with closing(get_db_connection()) as conn:
            row = conn.execute(
                "SELECT profile_photo FROM profiles WHERE username = ?",
                (username,),
            ).fetchone()
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Database error")

    if row is None:
        raise HTTPException(status_code=404, detail="Profile photo not found")

    return Response(content=row["profile_photo"], media_type="image/png", status_code=200)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)