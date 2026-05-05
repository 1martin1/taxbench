import os
import uuid
import mimetypes
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, HTTPException, status
from fastapi.responses import JSONResponse, FileResponse
import uvicorn

app = FastAPI(
    title="Image Upload and Share API",
    description="An API for uploading images and generating shareable links.",
    version="1.0.0",
)

# Directory where images will be stored
BASE_DIR = Path(__file__).parent
IMAGE_DIR = BASE_DIR / "uploaded_images"
IMAGE_DIR.mkdir(exist_ok=True)


def _save_upload_file(upload_file: UploadFile) -> str:
    """
    Saves the uploaded file to disk and returns the generated image ID.
    The file is stored as <uuid>.<original_extension>.
    """
    # Generate a unique identifier
    image_id = uuid.uuid4().hex

    # Preserve original extension if possible
    original_filename = upload_file.filename
    _, ext = os.path.splitext(original_filename)
    ext = ext.lower()

    # Build the storage filename
    storage_name = f"{image_id}{ext}"
    storage_path = IMAGE_DIR / storage_name

    # Write file to disk
    try:
        with storage_path.open("wb") as buffer:
            while content := upload_file.file.read(1024 * 1024):
                buffer.write(content)
    finally:
        upload_file.file.close()

    return image_id


def _find_image_path(image_id: str) -> Path | None:
    """
    Locate the image file on disk based on the image_id.
    Returns the Path if found, otherwise None.
    """
    # Look for a file that starts with the image_id
    for entry in IMAGE_DIR.iterdir():
        if entry.is_file() and entry.name.startswith(image_id):
            return entry
    return None


@app.post("/upload", summary="Upload an image. Accept all formats.", response_model=dict)
async def upload_image(file: UploadFile = File(...)):
    """
    Accepts an image file via multipart/form-data and returns a JSON object
    containing the generated image identifier.
    """
    if not file:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No file provided."
        )

    try:
        image_id = _save_upload_file(file)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save file: {exc}"
        )

    return {"id": image_id}


@app.get(
    "/images/{image_id}",
    summary="Share an image",
    responses={
        200: {
            "content": {"*/*": {}},
            "description": "The image file being viewed."
        },
        404: {
            "content": {"application/json": {"example": {"error": "Image not found"}}},
            "description": "Image not found"
        },
        500: {
            "content": {"application/json": {"example": {"error": "Internal server error"}}},
            "description": "Internal server error"
        },
    },
)
async def get_image(image_id: str):
    """
    Returns the image file for the given identifier.
    The original MIME type is preserved and the response is set to display inline.
    """
    image_path = _find_image_path(image_id)
    if not image_path or not image_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found."
        )

    # Guess MIME type; default to octet-stream if unknown
    mime_type, _ = mimetypes.guess_type(str(image_path))
    if mime_type is None:
        mime_type = "application/octet-stream"

    # Set Content-Disposition to inline to discourage download prompts
    headers = {"Content-Disposition": f'inline; filename="{image_path.name}"'}

    try:
        return FileResponse(
            path=str(image_path),
            media_type=mime_type,
            headers=headers,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read image: {exc}"
        )


# Custom exception handlers to match the OpenAPI error schemas
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    content = {"error": exc.detail}
    return JSONResponse(status_code=exc.status_code, content=content)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)