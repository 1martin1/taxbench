import os
import uuid
import mimetypes
from pathlib import Path
from typing import Dict, Tuple

from fastapi import FastAPI, File, HTTPException, UploadFile, status, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
import uvicorn

# ---------- Configuration ----------
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# Maximum upload size: 10 MB
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # bytes

# In‑memory mapping: image_id -> (file_path, mime_type)
image_store: Dict[str, Tuple[Path, str]] = {}

# ---------- FastAPI app ----------
app = FastAPI(
    title="Image Upload and Share API",
    description="An API for uploading images and generating shareable links.",
    version="1.0.0",
)


# ---------- Pydantic models ----------
class UploadResponse(BaseModel):
    id: str


class ErrorResponse(BaseModel):
    error: str


# ---------- Exception handling ----------
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """
    Convert HTTPException into the error schema defined in the OpenAPI spec.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail},
    )


# ---------- Endpoints ----------
@app.post(
    "/upload",
    summary="Upload an image. Accept all formats.",
    response_model=UploadResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Bad request, invalid input"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def upload_image(file: UploadFile = File(...)):
    """
    Accept an image file, store it on disk and return a unique identifier.
    """
    if not file:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No file provided.",
        )

    # Read file content (size check)
    contents = await file.read()
    if len(contents) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File size exceeds the allowed limit of 10 MB.",
        )

    # Generate a unique identifier for the image
    image_id = uuid.uuid4().hex

    # Preserve original extension (helps with debugging)
    suffix = Path(file.filename).suffix
    filename = f"{image_id}{suffix}"
    file_path = UPLOAD_DIR / filename

    try:
        # Write to disk
        with open(file_path, "wb") as f:
            f.write(contents)

        # Determine MIME type based on file extension (avoid client‑supplied type)
        mime_type, _ = mimetypes.guess_type(str(file_path))
        if mime_type is None:
            mime_type = "application/octet-stream"

        # Store mapping for later retrieval
        image_store[image_id] = (file_path, mime_type)

        return {"id": image_id}
    except Exception:
        # Cleanup on failure
        if file_path.exists():
            file_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store the uploaded file.",
        )


@app.get(
    "/images/{imageId}",
    summary="Share an image",
    responses={
        200: {
            "content": {"*/*": {}},
            "description": "The image file being viewed. Mimetype of original image is preserved.",
        },
        404: {"model": ErrorResponse, "description": "Image not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
def get_image(imageId: str):
    """
    Retrieve the image bytes and return them with the original MIME type.
    The response is set to be displayed inline in browsers.
    """
    entry = image_store.get(imageId)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found",
        )

    file_path, mime_type = entry
    try:
        with open(file_path, "rb") as f:
            data = f.read()
        # Inline display; browsers will render the image directly.
        headers = {"Content-Disposition": f'inline; filename="{file_path.name}"'}
        return Response(content=data, media_type=mime_type, headers=headers)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read the image file.",
        )


# ---------- Application entry point ----------
if __name__ == "__main__":
    # Run the application on 0.0.0.0:5000
    uvicorn.run(app, host="0.0.0.0", port=5000, reload=False)