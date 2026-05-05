import os
import uuid
import mimetypes
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, status
from fastapi.responses import JSONResponse, FileResponse

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
    Save the uploaded file to disk and return the generated image ID.
    The file is saved as <uuid4><original_extension>.
    """
    # Generate a unique identifier
    image_id = str(uuid.uuid4())

    # Preserve original extension (if any)
    _, ext = os.path.splitext(upload_file.filename or "")
    ext = ext.lower()

    # Build the storage path
    stored_filename = f"{image_id}{ext}"
    stored_path = IMAGE_DIR / stored_filename

    # Write file to disk
    try:
        with stored_path.open("wb") as buffer:
            while content := upload_file.file.read(1024 * 1024):
                buffer.write(content)
    finally:
        upload_file.file.close()

    return image_id


def _find_image_path(image_id: str) -> Optional[Path]:
    """
    Locate the stored image file for a given image_id.
    Returns the Path if found, otherwise None.
    """
    # Search for a file that starts with the image_id (extension may vary)
    for candidate in IMAGE_DIR.iterdir():
        if candidate.is_file() and candidate.name.startswith(image_id):
            return candidate
    return None


@app.post("/upload", summary="Upload an image. Accept all formats.", response_model=dict)
async def upload_image(file: UploadFile = File(...)):
    """
    Accept an image file, store it, and return a shareable identifier.
    """
    if not file:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "No file provided."},
        )

    try:
        image_id = _save_upload_file(file)
        return {"id": image_id}
    except Exception as exc:
        # Log exception in real world scenario
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": f"Failed to save file: {str(exc)}"},
        )


@app.get(
    "/images/{image_id}",
    summary="Share an image",
    responses={
        200: {
            "content": {"*/*": {"schema": {"type": "string", "format": "binary"}}},
            "description": "The image file being viewed. Mimetype of original image is preserved.",
        },
        404: {"description": "Image not found", "content": {"application/json": {"example": {"error": "Not found"}}}},
        500: {"description": "Internal server error", "content": {"application/json": {"example": {"error": "Server error"}}}},
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

    # Guess MIME type based on file extension; default to octet-stream
    mime_type, _ = mimetypes.guess_type(str(image_path))
    mime_type = mime_type or "application/octet-stream"

    # Return file with inline disposition so browsers display it rather than download
    return FileResponse(
        path=image_path,
        media_type=mime_type,
        headers={"Content-Disposition": "inline"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)