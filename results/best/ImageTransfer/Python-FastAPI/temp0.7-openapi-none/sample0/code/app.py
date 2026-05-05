import os
import uuid
import json
import mimetypes
import imghdr
from pathlib import Path
from threading import Lock
from typing import Dict
import re

from fastapi import FastAPI, File, UploadFile, HTTPException, Response, status, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

# ---------- Configuration ----------
BASE_DIR = Path(__file__).parent
IMAGE_DIR = BASE_DIR / "images"
MAPPING_FILE = IMAGE_DIR / "mapping.json"
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB
ALLOWED_IMAGE_TYPES = {
    "jpeg": ".jpg",
    "png": ".png",
    "gif": ".gif",
    "bmp": ".bmp",
    "webp": ".webp",
    "tiff": ".tiff",
}

# Опасные паттерны для GIF
DANGEROUS_GIF_PATTERNS = [
    b"<script",
    b"<?php",
    b"<%",
    b"<html",
    b"<body",
    b"javascript:",
    b"onload=",
    b"onerror=",
]
# -----------------------------------

app = FastAPI(
    title="Image Upload and Share API",
    description="An API for uploading images and generating shareable links.",
    version="1.0.0",
)

# Ensure the image directory exists
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

# In‑memory mapping and its lock
_id_to_filename: Dict[str, str] = {}
_mapping_lock = Lock()


def _load_mapping() -> None:
    """Load the id→filename mapping from disk (if present)."""
    if MAPPING_FILE.exists():
        try:
            with MAPPING_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    _id_to_filename.update(data)
        except Exception:
            pass


def _save_mapping() -> None:
    """Persist the current mapping atomically."""
    temp_file = MAPPING_FILE.with_suffix(".tmp")
    with temp_file.open("w", encoding="utf-8") as f:
        json.dump(_id_to_filename, f)
    os.chmod(temp_file, 0o600)
    os.replace(temp_file, MAPPING_FILE)


_load_mapping()


def validate_image_content(content: bytes) -> str:
    """
    Validate image content using imghdr and additional checks for dangerous content.
    Returns image_type or raises HTTPException.
    """
    # Проверка через imghdr
    image_type = imghdr.what(None, h=content[:512])
    if image_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported or invalid image format.",
        )
    if image_type == "gif":
        for pattern in DANGEROUS_GIF_PATTERNS:
            if pattern in content[:1024]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid image format.",
                )

    return image_type


class UploadResponse(BaseModel):
    id: str


@app.post(
    "/upload",
    summary="Upload an image. Accept all formats.",
    response_model=UploadResponse,
    responses={
        200: {"description": "Successful upload"},
        400: {"description": "Bad request, invalid input"},
        500: {"description": "Internal server error"},
    },
)
async def upload_image(file: UploadFile = File(None)):
    """Upload an image file."""
    if file is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No file provided.",
        )
    if file.filename and (".." in file.filename or "/" in file.filename or "\\" in file.filename):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid filename.",
        )

    # Read file
    total_read = 0
    chunks = []
    try:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total_read += len(chunk)
            if total_read > MAX_UPLOAD_SIZE:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="File size exceeds the allowed limit.",
                )
            chunks.append(chunk)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read uploaded file.",
        )
    finally:
        await file.close()

    full_content = b"".join(chunks)
    image_type = validate_image_content(full_content)

    image_id = str(uuid.uuid4())
    extension = ALLOWED_IMAGE_TYPES[image_type]
    stored_filename = f"{image_id}{extension}"
    file_path = IMAGE_DIR / stored_filename

    try:
        with file_path.open("wb") as out_file:
            out_file.write(full_content)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store the uploaded file.",
        )

    with _mapping_lock:
        _id_to_filename[image_id] = stored_filename
        try:
            _save_mapping()
        except Exception:
            file_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to persist file mapping.",
            )

    return UploadResponse(id=image_id)


@app.get(
    "/images/{imageId}",
    summary="Share an image",
    responses={
        200: {"description": "The image file being viewed."},
        404: {"description": "Image not found"},
        500: {"description": "Internal server error"},
    },
)
def get_image(imageId: str):
    """Retrieve an image by its identifier."""
    if ".." in imageId or "/" in imageId or "\\" in imageId:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found.",
        )
    try:
        uuid.UUID(imageId)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found.",
        )

    with _mapping_lock:
        stored_filename = _id_to_filename.get(imageId)

    if not stored_filename:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found.",
        )

    file_path = IMAGE_DIR / stored_filename
    try:
        file_path.resolve().relative_to(IMAGE_DIR.resolve())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found.",
        )

    if not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image file missing.",
        )

    try:
        content = file_path.read_bytes()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read image file.",
        )

    mime_type, _ = mimetypes.guess_type(stored_filename)
    if not mime_type:
        mime_type = "application/octet-stream"

    headers = {
        "Content-Disposition": "inline",
        "X-Content-Type-Options": "nosniff",
    }
    return Response(content=content, media_type=mime_type, headers=headers)


# ---------- Exception handlers ----------
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail if isinstance(exc.detail, str) else str(exc.detail)},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "Internal server error"},
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)