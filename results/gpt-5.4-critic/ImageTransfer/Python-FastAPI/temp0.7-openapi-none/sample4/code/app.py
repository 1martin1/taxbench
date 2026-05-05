import json
import os
import secrets
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

try:
    import multipart  # type: ignore  # noqa: F401
    MULTIPART_AVAILABLE = True
except Exception:
    MULTIPART_AVAILABLE = False


BASE_DIR = Path(__file__).resolve().parent
STORAGE_DIR = BASE_DIR / "uploaded_images"
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MiB
SNIFF_BYTES = 64
SAFE_IMAGE_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)

app = FastAPI(
    title="Image Upload and Share API",
    description="An API for uploading images and generating shareable links.",
    version="1.0.0",
)


class UploadSuccessResponse(BaseModel):
    id: str


class ErrorResponse(BaseModel):
    error: str


def _is_safe_image_id(image_id: str) -> bool:
    return bool(image_id) and all(ch in SAFE_IMAGE_ID_CHARS for ch in image_id)


def _image_path(image_id: str) -> Path:
    if not _is_safe_image_id(image_id):
        raise ValueError("Invalid image id")
    return STORAGE_DIR / image_id


def _meta_path(image_id: str) -> Path:
    if not _is_safe_image_id(image_id):
        raise ValueError("Invalid image id")
    return STORAGE_DIR / f"{image_id}.json"


def _sniff_image_mime(header: bytes) -> str | None:
    if header.startswith(b"\xFF\xD8\xFF"):
        return "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if header.startswith(b"BM"):
        return "image/bmp"
    if header.startswith(b"II*\x00") or header.startswith(b"MM\x00*"):
        return "image/tiff"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    return None


def _read_metadata(image_id: str) -> dict | None:
    meta_path = _meta_path(image_id)
    if not meta_path.exists() or not meta_path.is_file():
        return None
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("mime_type"), str):
            return data
    except Exception:
        return None
    return None


def _write_metadata(image_id: str, mime_type: str) -> None:
    meta_path = _meta_path(image_id)
    temp_path = meta_path.with_suffix(".json.tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump({"mime_type": mime_type}, f)
    os.replace(temp_path, meta_path)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content={"error": "Bad request, invalid input"})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
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
async def upload_image(file: UploadFile = File(...)):
    if not MULTIPART_AVAILABLE:
        return JSONResponse(status_code=500, content={"error": "Internal server error"})

    image_id = secrets.token_urlsafe(16)
    path = _image_path(image_id)

    total_size = 0
    first_chunk = b""
    detected_mime = None
    chunk_size = 1024 * 1024

    try:
        with open(path, "wb") as out_file:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break

                if not first_chunk:
                    first_chunk = chunk[:SNIFF_BYTES]
                    detected_mime = _sniff_image_mime(first_chunk)

                total_size += len(chunk)
                if total_size > MAX_UPLOAD_SIZE:
                    out_file.close()
                    try:
                        path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    return JSONResponse(status_code=400, content={"error": "File is too large"})

                out_file.write(chunk)
    finally:
        await file.close()

    if total_size == 0:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        return JSONResponse(status_code=400, content={"error": "File is empty"})

    if detected_mime is None:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        return JSONResponse(status_code=400, content={"error": "Invalid image file"})

    _write_metadata(image_id, detected_mime)
    return {"id": image_id}


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
    try:
        path = _image_path(imageId)
    except ValueError:
        return JSONResponse(status_code=404, content={"error": "Image not found"})

    if not path.exists() or not path.is_file():
        return JSONResponse(status_code=404, content={"error": "Image not found"})

    metadata = _read_metadata(imageId)
    mime_type = metadata["mime_type"] if metadata and metadata.get("mime_type") else "application/octet-stream"

    headers = {
        "Content-Disposition": 'inline; filename="image"',
        "X-Content-Type-Options": "nosniff",
    }
    return FileResponse(path=path, media_type=mime_type, headers=headers)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)