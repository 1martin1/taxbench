import os
import uuid
import mimetypes
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, UploadFile, HTTPException, status, Request
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
import uvicorn

# ------------------------------
# Configuration
# ------------------------------
BASE_DIR = Path(__file__).parent
IMAGE_DIR = BASE_DIR / "uploaded_images"
IMAGE_DIR.mkdir(exist_ok=True)

# Maximum upload size: 5 MB
MAX_UPLOAD_SIZE = 5 * 1024 * 1024

# Allowed image MIME types
ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/tiff",
    "image/svg+xml",
}

# ------------------------------
# FastAPI app
# ------------------------------
app = FastAPI(
    title="Image Upload and Share API",
    description="An API for uploading images and generating shareable links.",
    version="1.0.0",
)


class UploadResponse(BaseModel):
    id: str


# ------------------------------
# Helper functions
# ------------------------------
async def _save_upload_file(upload_file: UploadFile) -> str:
    """
    Validate and save the uploaded image file to disk.
    Returns the generated image identifier.
    """
    # Validate MIME type
    if upload_file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file type.",
        )

    # Generate a unique identifier
    image_id = uuid.uuid4().hex

    # Preserve original extension (if any)
    _, ext = os.path.splitext(upload_file.filename)
    ext = ext.lower()
    if not ext:
        # Fallback to extension based on MIME type
        ext = mimetypes.guess_extension(upload_file.content_type) or ""

    storage_name = f"{image_id}{ext}"
    storage_path = IMAGE_DIR / storage_name

    total_bytes = 0
    chunk_size = 1024 * 1024  # 1 MB

    try:
        with storage_path.open("wb") as buffer:
            while True:
                chunk = await upload_file.read(chunk_size)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > MAX_UPLOAD_SIZE:
                    # Remove partially written file
                    buffer.close()
                    storage_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="File size exceeds the allowed limit.",
                    )
                buffer.write(chunk)
    finally:
        await upload_file.close()

    return image_id


def _find_image_path(image_id: str) -> Optional[Path]:
    """
    Locate the stored image file based on its identifier.
    Returns the Path if found, otherwise None.
    """
    for entry in IMAGE_DIR.iterdir():
        if entry.is_file() and entry.name.startswith(image_id):
            return entry
    return None


# ------------------------------
# API Endpoints
# ------------------------------
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
async def upload_image(file: UploadFile = File(...)):
    """
    Accept an image file via multipart/form-data and return a JSON object
    containing the generated image identifier.
    """
    if not file:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No file provided.",
        )

    image_id = await _save_upload_file(file)
    return UploadResponse(id=image_id)


@app.get(
    "/images/{imageId}",
    summary="Share an image",
    responses={
        200: {
            "description": "The image file being viewed. Mimetype of original image is preserved.",
            "content": {"*/*": {}},
        },
        404: {
            "description": "Image not found",
            "content": {"application/json": {"example": {"error": "Image not found"}}},
        },
        500: {
            "description": "Internal server error",
            "content": {"application/json": {"example": {"error": "Internal server error"}}},
        },
    },
)
async def get_image(imageId: str):
    """
    Return the image file for the given identifier.
    The original MIME type is preserved and the response is set to display inline.
    """
    image_path = _find_image_path(imageId)
    if not image_path or not image_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found.",
        )

    mime_type, _ = mimetypes.guess_type(str(image_path))
    if mime_type is None:
        mime_type = "application/octet-stream"

    headers = {"Content-Disposition": f'inline; filename="{image_path.name}"'}

    return FileResponse(
        path=str(image_path),
        media_type=mime_type,
        headers=headers,
    )


# ------------------------------
# Custom error handler to match OpenAPI schema
# ------------------------------
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


# ------------------------------
# Application entry point
# ------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000, reload=False)