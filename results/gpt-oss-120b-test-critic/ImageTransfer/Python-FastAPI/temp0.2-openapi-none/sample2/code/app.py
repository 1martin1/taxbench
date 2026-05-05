import os
import uuid
import imghdr
import mimetypes
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, status
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

app = FastAPI(
    title="Image Upload and Share API",
    description="An API for uploading images and generating shareable links.",
    version="1.0.0",
)

# Directory where images will be stored
BASE_DIR = Path(__file__).parent
IMAGE_DIR = BASE_DIR / "uploaded_images"
IMAGE_DIR.mkdir(exist_ok=True)

# Allowed image types and their extensions
ALLOWED_IMAGE_TYPES = {
    "jpeg": ".jpg",
    "png": ".png",
    "gif": ".gif",
    "bmp": ".bmp",
    "webp": ".webp",
    "tiff": ".tiff",
    "rgb": ".rgb",
    "pbm": ".pbm",
    "pgm": ".pgm",
    "ppm": ".ppm",
    "rast": ".rast",
    "xbm": ".xbm",
}


class UploadResponse(BaseModel):
    id: str


class ErrorResponse(BaseModel):
    error: str


def _write_file(image_id: str, ext: str, data: bytes) -> Path:
    """
    Synchronously write the uploaded image data to disk.
    Returns the path of the saved file.
    """
    filename = f"{image_id}{ext}"
    file_path = IMAGE_DIR / filename
    with file_path.open("wb") as f:
        f.write(data)
    return file_path


def _find_image_path(image_id: str) -> Optional[Path]:
    """
    Locate the stored image file for a given image_id.
    Returns the Path if found, otherwise None.
    """
    for candidate in IMAGE_DIR.iterdir():
        if candidate.is_file() and candidate.name.startswith(image_id):
            return candidate
    return None


@app.post(
    "/upload",
    summary="Upload an image. Accept all formats.",
    response_model=UploadResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def upload_image(file: UploadFile = File(...)):
    """
    Accept an image file, validate its content, store it, and return a shareable identifier.
    """
    if not file:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No file provided.",
        )

    # Read the entire content (FastAPI's UploadFile provides async read)
    try:
        content = await file.read()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read uploaded file: {str(exc)}",
        )

    # Validate that the content is a supported image type
    image_type = imghdr.what(None, h=content)
    if image_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file type. Only image files are allowed.",
        )

    ext = ALLOWED_IMAGE_TYPES[image_type]
    image_id = str(uuid.uuid4())

    # Write file to disk in a thread pool to avoid blocking the event loop
    try:
        await run_in_threadpool(_write_file, image_id, ext, content)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save file: {str(exc)}",
        )
    finally:
        await file.close()

    return UploadResponse(id=image_id)


@app.get(
    "/images/{image_id}",
    summary="Share an image",
    responses={
        200: {
            "content": {"*/*": {"schema": {"type": "string", "format": "binary"}}},
            "description": "The image file being viewed. Mimetype of original image is preserved.",
        },
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def get_image(image_id: str):
    """
    Retrieve the image by its identifier and return it with the original MIME type.
    The response is set to be displayed inline in browsers.
    """
    image_path = _find_image_path(image_id)
    if not image_path or not image_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found",
        )

    # Determine MIME type based on file extension
    mime_type, _ = mimetypes.guess_type(str(image_path))
    # Ensure we only serve image MIME types
    if not mime_type or not mime_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored file is not a valid image.",
        )
    mime_type = mime_type or "application/octet-stream"

    return FileResponse(
        path=image_path,
        media_type=mime_type,
        headers={"Content-Disposition": "inline"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)