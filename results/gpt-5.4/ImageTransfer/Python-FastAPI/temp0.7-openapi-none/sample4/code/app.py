import imghdr
import mimetypes
import os
import secrets
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel


BASE_DIR = Path(__file__).resolve().parent
STORAGE_DIR = BASE_DIR / "uploaded_images"
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="Image Upload and Share API",
    description="An API for uploading images and generating shareable links.",
    version="1.0.0",
)


class UploadSuccessResponse(BaseModel):
    id: str


class ErrorResponse(BaseModel):
    error: str


def _safe_image_id() -> str:
    return secrets.token_urlsafe(16)


def _detect_content_type(filename: str, data: bytes) -> str:
    detected = imghdr.what(None, h=data)
    if detected:
        if detected == "jpeg":
            return "image/jpeg"
        return f"image/{detected}"

    guessed, _ = mimetypes.guess_type(filename or "")
    if guessed:
        return guessed

    return "application/octet-stream"


def _is_image_content_type(content_type: str) -> bool:
    return content_type.startswith("image/")


def _metadata_path(image_id: str) -> Path:
    return STORAGE_DIR / f"{image_id}.meta"


def _data_path(image_id: str) -> Path:
    return STORAGE_DIR / f"{image_id}.bin"


def _write_image(image_id: str, content_type: str, data: bytes) -> None:
    _data_path(image_id).write_bytes(data)
    _metadata_path(image_id).write_text(content_type, encoding="utf-8")


def _read_image(image_id: str) -> tuple[str, bytes] | None:
    meta_path = _metadata_path(image_id)
    data_path = _data_path(image_id)

    if not meta_path.exists() or not data_path.exists():
        return None

    content_type = meta_path.read_text(encoding="utf-8").strip() or "application/octet-stream"
    data = data_path.read_bytes()
    return content_type, data


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, str):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})
    return JSONResponse(status_code=exc.status_code, content={"error": "Request failed"})


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
    if file is None:
        raise HTTPException(status_code=400, detail="Missing file")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    content_type = _detect_content_type(file.filename or "", data)
    if not _is_image_content_type(content_type):
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid image")

    image_id = _safe_image_id()
    _write_image(image_id, content_type, data)

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
    image_record = _read_image(imageId)
    if image_record is None:
        raise HTTPException(status_code=404, detail="Image not found")

    content_type, data = image_record
    headers = {
        "Content-Disposition": "inline",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "public, max-age=31536000, immutable",
    }
    return Response(content=data, media_type=content_type, headers=headers)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)