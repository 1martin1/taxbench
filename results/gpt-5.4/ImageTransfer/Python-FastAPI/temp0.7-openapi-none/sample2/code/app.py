import imghdr
import mimetypes
import os
import secrets
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, HTTPException, Path as FastAPIPath, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploaded_images"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="Image Upload and Share API",
    description="An API for uploading images and generating shareable links.",
    version="1.0.0",
)


class UploadSuccessResponse(BaseModel):
    id: str


class ErrorResponse(BaseModel):
    error: str


def _safe_extension(filename: str | None) -> str:
    if not filename:
        return ""
    suffix = Path(filename).suffix
    if not suffix:
        return ""
    return suffix[:20]


def _detect_content_type(data: bytes, filename: str | None) -> str:
    detected = imghdr.what(None, h=data)
    if detected:
        if detected == "jpeg":
            return "image/jpeg"
        if detected == "png":
            return "image/png"
        if detected == "gif":
            return "image/gif"
        if detected == "bmp":
            return "image/bmp"
        if detected == "webp":
            return "image/webp"
        if detected == "tiff":
            return "image/tiff"

    guessed, _ = mimetypes.guess_type(filename or "")
    if guessed and guessed.startswith("image/"):
        return guessed

    return "application/octet-stream"


def _build_storage_path(image_id: str, original_filename: str | None) -> Path:
    extension = _safe_extension(original_filename)
    return UPLOAD_DIR / f"{image_id}{extension}"


def _find_image_file(image_id: str) -> Path | None:
    exact = UPLOAD_DIR / image_id
    if exact.is_file():
        return exact

    matches = list(UPLOAD_DIR.glob(f"{image_id}.*"))
    if matches:
        return matches[0]

    return None


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    if isinstance(exc.detail, str):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})
    return JSONResponse(status_code=exc.status_code, content={"error": "Request failed"})


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, __: Exception):
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


@app.post(
    "/upload",
    response_model=UploadSuccessResponse,
    responses={
        200: {"description": "Successful upload"},
        400: {"model": ErrorResponse, "description": "Bad request, invalid input"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
    summary="Upload an image. Accept all formats.",
    description="Allow users to upload images, returning a shareable link.",
)
async def upload_image(file: UploadFile = File(...)):
    if file is None:
        raise HTTPException(status_code=400, detail="No file provided")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    image_id = secrets.token_urlsafe(16)
    storage_path = _build_storage_path(image_id, file.filename)

    try:
        with open(storage_path, "wb") as f:
            f.write(data)
    except OSError:
        raise HTTPException(status_code=500, detail="Internal server error")

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
async def get_image(imageId: str = FastAPIPath(..., description="The ID of the image to be viewed.")):
    image_path = _find_image_file(imageId)
    if image_path is None or not image_path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")

    try:
        data = image_path.read_bytes()
    except OSError:
        raise HTTPException(status_code=500, detail="Internal server error")

    media_type = _detect_content_type(data, image_path.name)
    headers = {
        "Content-Disposition": f'inline; filename="{image_path.name}"',
        "X-Content-Type-Options": "nosniff",
    }
    return Response(content=data, media_type=media_type, headers=headers)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)