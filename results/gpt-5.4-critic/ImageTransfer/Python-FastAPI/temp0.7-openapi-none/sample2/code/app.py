import imghdr
import json
import mimetypes
import os
import re
import secrets
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MiB
CHUNK_SIZE = 1024 * 1024
IMAGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
META_SUFFIX = ".meta.json"


class UploadSuccessResponse(BaseModel):
    id: str
    link: str


class ErrorResponse(BaseModel):
    error: str


app = FastAPI(
    title="Image Upload and Share API",
    description="An API for uploading images and generating shareable links.",
    version="1.0.0",
)


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return JSONResponse(status_code=exc.status_code, content={"error": detail})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    for error in exc.errors():
        loc = error.get("loc", ())
        if "file" in loc:
            return JSONResponse(status_code=400, content={"error": "No file provided"})
    return JSONResponse(status_code=400, content={"error": "Bad request"})


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _guess_extension(filename: Optional[str], content_type: Optional[str], header_data: bytes) -> str:
    if filename:
        suffix = Path(filename).suffix
        if suffix:
            return suffix.lower()

    detected_image_type = imghdr.what(None, h=header_data)
    if detected_image_type:
        if detected_image_type == "jpeg":
            return ".jpg"
        return f".{detected_image_type}"

    if content_type:
        guessed = mimetypes.guess_extension(content_type)
        if guessed:
            return guessed.lower()

    return ""


def _detect_image_media_type(header_data: bytes) -> Optional[str]:
    detected_image_type = imghdr.what(None, h=header_data)
    if not detected_image_type:
        return None
    if detected_image_type == "jpeg":
        return "image/jpeg"
    return f"image/{detected_image_type}"


def _sanitize_filename(filename: str) -> str:
    cleaned = filename.replace('"', "").replace("\r", "").replace("\n", "")
    return Path(cleaned).name or "image"


def _meta_path_for(image_id: str) -> Path:
    return UPLOAD_DIR / f"{image_id}{META_SUFFIX}"


def _image_path_for(image_id: str, extension: str) -> Path:
    return UPLOAD_DIR / f"{image_id}{extension}"


def _write_metadata(meta_path: Path, media_type: str, extension: str, original_filename: str) -> None:
    metadata = {
        "media_type": media_type,
        "extension": extension,
        "original_filename": original_filename,
    }
    with open(meta_path, "w", encoding="utf-8") as meta_file:
        json.dump(metadata, meta_file)


def _read_metadata(meta_path: Path) -> dict:
    with open(meta_path, "r", encoding="utf-8") as meta_file:
        return json.load(meta_file)


def _build_link(request: Request, image_id: str) -> str:
    return str(request.url_for("get_image", imageId=image_id))


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
async def upload_image(request: Request, file: UploadFile = File(...)) -> UploadSuccessResponse:
    image_id = secrets.token_urlsafe(16)
    temp_path = UPLOAD_DIR / f"{image_id}.uploading"
    total_size = 0
    header_data = b""

    try:
        with open(temp_path, "wb") as output_file:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break

                total_size += len(chunk)
                if total_size > MAX_UPLOAD_SIZE:
                    raise HTTPException(status_code=400, detail="Uploaded file is too large")

                if len(header_data) < 512:
                    needed = 512 - len(header_data)
                    header_data += chunk[:needed]

                output_file.write(chunk)
    finally:
        await file.close()

    if total_size == 0:
        _safe_unlink(temp_path)
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    media_type = _detect_image_media_type(header_data)
    if media_type is None:
        _safe_unlink(temp_path)
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid image")

    extension = _guess_extension(file.filename, media_type, header_data)
    final_path = _image_path_for(image_id, extension)
    meta_path = _meta_path_for(image_id)

    try:
        os.replace(temp_path, final_path)
        _write_metadata(
            meta_path=meta_path,
            media_type=media_type,
            extension=extension,
            original_filename=_sanitize_filename(file.filename or f"{image_id}{extension}"),
        )
    except OSError:
        _safe_unlink(temp_path)
        _safe_unlink(final_path)
        _safe_unlink(meta_path)
        raise HTTPException(status_code=500, detail="Internal server error")

    return UploadSuccessResponse(id=image_id, link=_build_link(request, image_id))


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
async def get_image(imageId: str) -> Response:
    if not IMAGE_ID_PATTERN.fullmatch(imageId):
        raise HTTPException(status_code=404, detail="Image not found")

    meta_path = _meta_path_for(imageId)

    try:
        metadata = _read_metadata(meta_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Image not found")
    except (OSError, ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=500, detail="Internal server error")

    extension = metadata.get("extension")
    media_type = metadata.get("media_type")
    original_filename = metadata.get("original_filename")

    if not isinstance(extension, str) or not isinstance(media_type, str) or not isinstance(original_filename, str):
        raise HTTPException(status_code=500, detail="Internal server error")

    image_path = _image_path_for(imageId, extension)

    try:
        file_size = image_path.stat().st_size
        if file_size > MAX_UPLOAD_SIZE:
            raise HTTPException(status_code=500, detail="Internal server error")
        with open(image_path, "rb") as image_file:
            data = image_file.read()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Image not found")
    except OSError:
        raise HTTPException(status_code=500, detail="Internal server error")

    headers = {
        "Content-Disposition": 'inline; filename="{}"'.format(_sanitize_filename(original_filename)),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "public, max-age=31536000, immutable",
    }

    return Response(content=data, media_type=media_type, headers=headers)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)