import imghdr
import mimetypes
import os
import secrets
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, HTTPException, Path as PathParam, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploaded_images"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class UploadSuccessResponse(BaseModel):
    id: str


class ErrorResponse(BaseModel):
    error: str


app = FastAPI(
    title="Image Upload and Share API",
    description="An API for uploading images and generating shareable links.",
    version="1.0.0",
)


def _safe_extension(filename: str | None) -> str:
    if not filename:
        return ""
    suffix = Path(filename).suffix
    if not suffix:
        return ""
    if len(suffix) > 10:
        return ""
    return suffix.lower()


def _detect_mime(data: bytes, filename: str | None) -> str:
    detected = imghdr.what(None, h=data)
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


def _is_image_payload(data: bytes, filename: str | None) -> bool:
    mime = _detect_mime(data, filename)
    return mime.startswith("image/")


def _find_stored_file(image_id: str) -> Path | None:
    matches = list(UPLOAD_DIR.glob(f"{image_id}.*"))
    if matches:
        return matches[0]
    exact = UPLOAD_DIR / image_id
    if exact.exists() and exact.is_file():
        return exact
    return None


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    if isinstance(exc.detail, str):
        detail = exc.detail
    else:
        detail = "Request failed"
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
async def upload_image(file: UploadFile = File(...)):
    if file is None:
        raise HTTPException(status_code=400, detail="No file provided")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    if not _is_image_payload(content, file.filename):
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid image")

    image_id = secrets.token_urlsafe(16)
    extension = _safe_extension(file.filename)
    stored_name = f"{image_id}{extension}"
    stored_path = UPLOAD_DIR / stored_name

    with open(stored_path, "wb") as output_file:
        output_file.write(content)

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
async def get_image(
    imageId: str = PathParam(..., description="The ID of the image to be viewed.")
):
    stored_path = _find_stored_file(imageId)
    if stored_path is None:
        raise HTTPException(status_code=404, detail="Image not found")

    with open(stored_path, "rb") as image_file:
        content = image_file.read()

    media_type = _detect_mime(content, stored_path.name)

    headers = {
        "Content-Disposition": f'inline; filename="{stored_path.name}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "public, max-age=31536000, immutable",
    }

    return Response(content=content, media_type=media_type, headers=headers)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)