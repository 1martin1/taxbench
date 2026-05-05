import os
import uuid
from pathlib import Path
from typing import Dict, Tuple

from fastapi import FastAPI, File, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse, Response
import uvicorn

app = FastAPI(
    title="Image Upload and Share API",
    description="An API for uploading images and generating shareable links.",
    version="1.0.0",
)

# Directory to store uploaded images
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# In‑memory mapping: image_id -> (file_path, mime_type)
image_store: Dict[str, Tuple[Path, str]] = {}


@app.post("/upload", summary="Upload an image. Accept all formats.", response_model=dict)
async def upload_image(file: UploadFile = File(...)):
    """
    Accept an image file, store it on disk and return a unique identifier.
    """
    if not file:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No file provided.",
        )

    # Generate a unique identifier for the image
    image_id = uuid.uuid4().hex

    # Preserve original extension (optional, helps with debugging)
    suffix = Path(file.filename).suffix
    filename = f"{image_id}{suffix}"
    file_path = UPLOAD_DIR / filename

    try:
        # Read the entire file into memory (acceptable for demo purposes)
        contents = await file.read()
        # Write to disk
        with open(file_path, "wb") as f:
            f.write(contents)

        # Store mapping for later retrieval
        image_store[image_id] = (file_path, file.content_type or "application/octet-stream")

        return JSONResponse(content={"id": image_id}, status_code=status.HTTP_200_OK)
    except Exception as exc:
        # Cleanup if something went wrong
        if file_path.exists():
            file_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )


@app.get(
    "/images/{image_id}",
    summary="Share an image",
    responses={
        200: {
            "content": {"*/*": {}},
            "description": "The image file being viewed. Mimetype of original image is preserved.",
        },
        404: {"description": "Image not found"},
        500: {"description": "Internal server error"},
    },
)
def get_image(image_id: str):
    """
    Retrieve the image bytes and return them with the original MIME type.
    The response is set to be displayed inline in browsers.
    """
    entry = image_store.get(image_id)
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
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )


if __name__ == "__main__":
    # Run the application on 0.0.0.0:5000
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)