import os
import uuid
import mimetypes
import time
from pathlib import Path
from typing import Optional, List, Dict

from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
import uvicorn

app = FastAPI(
    title="Image Upload and Share API",
    description="An API for uploading images and generating shareable links.",
    version="1.0.0",
)

# Directory where images will be stored
BASE_DIR = Path(__file__).parent
IMAGE_DIR = BASE_DIR / "images"
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

# Configuration
MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # 5 MB
ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/bmp",
}
RATE_LIMIT_WINDOW = 60  # seconds
MAX_REQUESTS_PER_WINDOW = 30
_rate_limit_store: Dict[str, List[float]] = {}


def _error_response(status_code: int, message: str) -> JSONResponse:
    """
    Return a JSONResponse that matches the OpenAPI error schema.
    """
    return JSONResponse(status_code=status_code, content={"error": message})


def _check_rate_limit(ip: str) -> Optional[JSONResponse]:
    """
    Simple in‑memory rate limiting per IP address.
    """
    now = time.time()
    timestamps = _rate_limit_store.get(ip, [])
    # Keep only timestamps within the window
    timestamps = [ts for ts in timestamps if now - ts < RATE_LIMIT_WINDOW]
    if len(timestamps) >= MAX_REQUESTS_PER_WINDOW:
        return _error_response(429, "Too many requests, please try again later.")
    timestamps.append(now)
    _rate_limit_store[ip] = timestamps
    return None


def _save_upload_file(upload_file: UploadFile) -> str:
    """
    Save the uploaded file to disk, enforcing size limits and allowed MIME types.
    Returns the generated image identifier (without extension).
    """
    # Validate MIME type
    if upload_file.content_type not in ALLOWED_MIME_TYPES:
        raise ValueError("Unsupported file type.")

    # Generate a UUID for the image
    image_id = uuid.uuid4().hex

    # Determine proper extension from MIME type
    ext = mimetypes.guess_extension(upload_file.content_type) or ""
    file_path = IMAGE_DIR / f"{image_id}{ext}"

    total_written = 0
    try:
        with file_path.open("wb") as buffer:
            while True:
                chunk = upload_file.file.read(1024 * 1024)  # 1 MB chunks
                if not chunk:
                    break
                total_written += len(chunk)
                if total_written > MAX_UPLOAD_SIZE:
                    raise ValueError("File size exceeds the allowed limit.")
                buffer.write(chunk)
    finally:
        upload_file.file.close()

    return image_id


def _find_image_path(image_id: str) -> Optional[Path]:
    """
    Locate the image file on disk using the image_id (ignoring extension).
    Returns None if not found.
    """
    matches = list(IMAGE_DIR.glob(f"{image_id}.*"))
    if matches:
        return matches[0]
    return None


@app.post("/upload", summary="Upload an image. Accept all formats.")
async def upload_image(request: Request, file: UploadFile = File(...)):
    """
    Accept an image file, store it, and return a unique identifier.
    """
    # Rate limiting
    ip = request.client.host
    limit_response = _check_rate_limit(ip)
    if limit_response:
        return limit_response

    if not file:
        return _error_response(400, "No file provided.")

    try:
        image_id = _save_upload_file(file)
    except ValueError as ve:
        return _error_response(400, str(ve))
    except Exception:
        # Generic internal error without leaking details
        return _error_response(500, "Failed to save file.")

    return JSONResponse(content={"id": image_id})


@app.get(
    "/images/{imageId}",
    summary="Share an image",
    responses={
        200: {
            "description": "The image file being viewed. Mimetype of original image is preserved.",
            "content": {"*/*": {"schema": {"type": "string", "format": "binary"}}},
        },
        404: {"description": "Image not found"},
        500: {"description": "Internal server error"},
    },
)
async def get_image(imageId: str):
    """
    Retrieve an image by its identifier. The image is served inline so browsers display it
    rather than prompting a download.
    """
    try:
        image_path = _find_image_path(imageId)
        if not image_path or not image_path.is_file():
            return _error_response(404, "Image not found")
        # Determine MIME type; fallback to generic binary type
        mime_type, _ = mimetypes.guess_type(str(image_path))
        mime_type = mime_type or "application/octet-stream"

        # Serve the file with Content-Disposition: inline
        return FileResponse(
            path=image_path,
            media_type=mime_type,
            filename=image_path.name,
            headers={"Content-Disposition": f'inline; filename="{image_path.name}"'},
        )
    except Exception:
        return _error_response(500, "Internal server error")


if __name__ == "__main__":
    # Run the application on 0.0.0.0:5000
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)