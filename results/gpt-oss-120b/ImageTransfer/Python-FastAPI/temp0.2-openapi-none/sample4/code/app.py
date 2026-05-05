import os
import uuid
import mimetypes
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, UploadFile, HTTPException, Response
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI(
    title="Image Upload and Share API",
    description="An API for uploading images and generating shareable links.",
    version="1.0.0",
)

# Directory to store uploaded images
BASE_DIR = Path(__file__).parent
IMAGE_DIR = BASE_DIR / "uploaded_images"
IMAGE_DIR.mkdir(exist_ok=True)


def _save_upload_file(upload_file: UploadFile) -> str:
    """
    Save the uploaded file to disk and return the generated image ID.
    """
    # Generate a unique ID
    image_id = uuid.uuid4().hex

    # Preserve original extension if possible
    original_filename = upload_file.filename or ""
    _, ext = os.path.splitext(original_filename)
    ext = ext.lower()

    # Build file path
    filename = f"{image_id}{ext}"
    file_path = IMAGE_DIR / filename

    # Write file content
    try:
        with file_path.open("wb") as buffer:
            content = upload_file.file.read()
            buffer.write(content)
    finally:
        upload_file.file.close()

    return image_id


def _find_image_path(image_id: str) -> Optional[Path]:
    """
    Locate the image file on disk based on the image ID.
    Returns None if not found.
    """
    # Search for a file that starts with the image_id
    for path in IMAGE_DIR.iterdir():
        if path.is_file() and path.name.startswith(image_id):
            return path
    return None


@app.post("/upload", summary="Upload an image. Accept all formats.", description="Allow users to upload images, returning a shareable link.")
async def upload_image(file: UploadFile = File(...)):
    if not file:
        raise HTTPException(status_code=400, detail={"error": "No file provided."})

    # Basic validation: ensure it's an image based on MIME type (optional)
    # Accept any format as per specification, so we skip strict validation.

    try:
        image_id = _save_upload_file(file)
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": f"Failed to save file: {str(e)}"})

    return JSONResponse(content={"id": image_id})


@app.get(
    "/images/{image_id}",
    summary="Share an image",
    description="Look at an image in the browser.",
    responses={
        200: {
            "description": "The image file being viewed. Mimetype of original image is be preserved.",
            "content": {"*/*": {"schema": {"type": "string", "format": "binary"}}},
        },
        404: {"description": "Image not found", "content": {"application/json": {"example": {"error": "Not found"}}}},
        500: {"description": "Internal server error", "content": {"application/json": {"example": {"error": "Server error"}}}},
    },
)
async def get_image(image_id: str):
    image_path = _find_image_path(image_id)
    if not image_path or not image_path.is_file():
        raise HTTPException(status_code=404, detail={"error": "Image not found"})

    try:
        content = image_path.read_bytes()
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": f"Failed to read image: {str(e)}"})

    mime_type, _ = mimetypes.guess_type(str(image_path))
    if not mime_type:
        mime_type = "application/octet-stream"

    headers = {"Content-Disposition": "inline"}  # Hint browsers to display rather than download
    return Response(content=content, media_type=mime_type, headers=headers)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)