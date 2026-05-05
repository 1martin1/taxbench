import asyncio
import os
import re
import secrets
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_SIZE = 10 * 1024 * 1024
MAX_DOWNLOAD_SIZE = 10 * 1024 * 1024
MAX_STORED_FILES = 1000
UPLOAD_CHUNK_SIZE = 1024 * 1024
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
IMAGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

upload_semaphore = asyncio.Semaphore(8)
download_semaphore = asyncio.Semaphore(16)


class UploadSuccessResponse(BaseModel):
    id: str


class ErrorResponse(BaseModel):
    error: str


app = FastAPI(
    title="Image Upload and Share API",
    description="An API for uploading images and generating shareable links.",
    version="1.0.0",
)


def build_storage_path(image_id: str) -> Path:
    return UPLOAD_DIR / image_id


def is_valid_image_id(image_id: str) -> bool:
    return bool(IMAGE_ID_PATTERN.fullmatch(image_id))


def count_stored_files() -> int:
    count = 0
    try:
        for entry in UPLOAD_DIR.iterdir():
            if entry.is_file() and entry.suffix != ".meta":
                count += 1
    except OSError:
        raise HTTPException(status_code=500, detail="Internal server error")
    return count


def detect_content_type_from_bytes(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"BM"):
        return "image/bmp"
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if data.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    if data.startswith(b"<?xml") or b"<svg" in data[:512].lower():
        return "image/svg+xml"
    return "application/octet-stream"


def sanitize_meta_content_type(value: str) -> str:
    allowed = {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/bmp",
        "image/webp",
        "image/tiff",
        "image/x-icon",
        "image/svg+xml",
        "application/octet-stream",
    }
    value = value.strip().lower()
    if value in allowed:
        return value
    return "application/octet-stream"


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return JSONResponse(status_code=exc.status_code, content={"error": detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, __: Exception):
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


@app.post(
    "/upload",
    response_model=UploadSuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Bad request, invalid input"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
    summary="Upload an image. Accept all formats.",
    description="Allow users to upload images, returning a shareable link.",
)
async def upload_image(file: Optional[UploadFile] = File(default=None)):
    if file is None:
        raise HTTPException(status_code=400, detail="No file provided")

    async with upload_semaphore:
        if count_stored_files() >= MAX_STORED_FILES:
            raise HTTPException(status_code=500, detail="Internal server error")

        image_id = secrets.token_urlsafe(16)
        storage_path = build_storage_path(image_id)
        meta_path = Path(f"{storage_path}.meta")

        total_size = 0
        header_sample = b""

        try:
            with open(storage_path, "wb") as stored_file:
                while True:
                    chunk = await file.read(UPLOAD_CHUNK_SIZE)
                    if not chunk:
                        break

                    total_size += len(chunk)
                    if total_size > MAX_UPLOAD_SIZE:
                        raise HTTPException(status_code=400, detail="Uploaded file is too large")

                    if len(header_sample) < 1024:
                        remaining = 1024 - len(header_sample)
                        header_sample += chunk[:remaining]

                    stored_file.write(chunk)

            if total_size == 0:
                raise HTTPException(status_code=400, detail="Uploaded file is empty")

            content_type = detect_content_type_from_bytes(header_sample)

            with open(meta_path, "w", encoding="utf-8") as meta_file:
                meta_file.write(content_type)
        except HTTPException:
            try:
                if storage_path.exists():
                    storage_path.unlink()
            except OSError:
                pass
            try:
                if meta_path.exists():
                    meta_path.unlink()
            except OSError:
                pass
            raise
        except OSError:
            try:
                if storage_path.exists():
                    storage_path.unlink()
            except OSError:
                pass
            try:
                if meta_path.exists():
                    meta_path.unlink()
            except OSError:
                pass
            raise HTTPException(status_code=500, detail="Internal server error")
        finally:
            await file.close()

    return UploadSuccessResponse(id=image_id)


@app.get(
    "/images/{imageId}",
    responses={
        200: {
            "description": "The image file being viewed. Mimetype of original image is be preserved.",
            "content": {"*/*": {"schema": {"type": "string", "format": "binary"}}},
        },
        404: {"model": ErrorResponse, "description": "Image not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
    summary="Share an image",
    description="Look at an image in the browser.",
)
async def get_image(imageId: str):
    if not is_valid_image_id(imageId):
        raise HTTPException(status_code=404, detail="Image not found")

    storage_path = build_storage_path(imageId).resolve()
    meta_path = Path(f"{storage_path}.meta")

    try:
        upload_root = UPLOAD_DIR.resolve()
    except OSError:
        raise HTTPException(status_code=500, detail="Internal server error")

    if storage_path.parent != upload_root:
        raise HTTPException(status_code=404, detail="Image not found")

    if not storage_path.exists() or not storage_path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")

    async with download_semaphore:
        try:
            file_size = storage_path.stat().st_size
            if file_size > MAX_DOWNLOAD_SIZE:
                raise HTTPException(status_code=500, detail="Internal server error")

            with open(storage_path, "rb") as image_file:
                chunks = []
                total_read = 0
                while True:
                    chunk = image_file.read(DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    total_read += len(chunk)
                    if total_read > MAX_DOWNLOAD_SIZE:
                        raise HTTPException(status_code=500, detail="Internal server error")
                    chunks.append(chunk)

            data = b"".join(chunks)
            detected_mime = detect_content_type_from_bytes(data[:1024])

            mime_type = detected_mime
            if mime_type == "application/octet-stream" and meta_path.exists() and meta_path.is_file():
                with open(meta_path, "r", encoding="utf-8") as meta_file:
                    mime_type = sanitize_meta_content_type(meta_file.read())
        except HTTPException:
            raise
        except OSError:
            raise HTTPException(status_code=500, detail="Internal server error")

    headers = {
        "Content-Disposition": 'inline; filename="image"',
        "X-Content-Type-Options": "nosniff",
    }
    return Response(content=data, media_type=mime_type, headers=headers)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)