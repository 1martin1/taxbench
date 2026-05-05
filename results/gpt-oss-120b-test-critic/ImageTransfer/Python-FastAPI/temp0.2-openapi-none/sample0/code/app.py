import os
import uuid
import mimetypes
from pathlib import Path
from typing import Generator

from fastapi import FastAPI, UploadFile, File, HTTPException, status, Response
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI(
    title="Image Upload and Share API",
    description="An API for uploading images and generating shareable links.",
    version="1.0.0",
)

# Directory to store uploaded images
BASE_DIR = Path(__file__).parent
IMAGE_DIR = BASE_DIR / "images"
IMAGE_DIR.mkdir(exist_ok=True)

# Maximum allowed upload size (10 MiB)
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB


def _save_upload_file(upload_file: UploadFile) -> str:
    """
    Save the uploaded file to disk in a memory‑efficient way and return the generated image ID.
    """
    # Validate MIME type – only allow image/* for safety
    if not upload_file.content_type or not upload_file.content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only image files are allowed.",
        )

    # Generate a unique identifier
    image_id = uuid.uuid4().hex

    # Preserve original extension if any (fallback to .bin)
    suffix = Path(upload_file.filename or "").suffix or ".bin"
    filename = f"{image_id}{suffix}"
    file_path = IMAGE_DIR / filename

    total_read = 0
    try:
        with open(file_path, "wb") as dest:
            while True:
                chunk = upload_file.file.read(1024 * 1024)  # read in 1 MiB chunks
                if not chunk:
                    break
                total_read += len(chunk)
                if total_read > MAX_UPLOAD_SIZE:
                    # Remove partially written file
                    dest.close()
                    file_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"File size exceeds the limit of {MAX_UPLOAD_SIZE // (1024 * 1024)} MiB.",
                    )
                dest.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        # Cleanup on unexpected error
        file_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save uploaded file.",
        ) from e
    finally:
        upload_file.file.close()

    return image_id


def _find_image_path(image_id: str) -> Path:
    """
    Locate the image file on disk based on its ID.
    Returns the Path if found, otherwise raises HTTPException 404.
    """
    matches = list(IMAGE_DIR.glob(f"{image_id}*"))
    if not matches:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found.",
        )
    # Return the first match (there should be only one)
    return matches[0]


@app.post(
    "/upload",
    summary="Upload an image. Accept all formats.",
    response_model=dict,
    responses={
        200: {"description": "Successful upload"},
        400: {"description": "Bad request, invalid input"},
        413: {"description": "Payload too large"},
        500: {"description": "Internal server error"},
    },
)
async def upload_image(file: UploadFile = File(...)):
    """
    Allow users to upload images, returning a shareable link (image ID).
    """
    if not file:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No file provided.",
        )
    image_id = _save_upload_file(file)
    return {"id": image_id}


@app.get(
    "/images/{imageId}",
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
def get_image(imageId: str):
    """
    Retrieve an image for viewing in the browser.
    """
    try:
        image_path = _find_image_path(imageId)
        mime_type, _ = mimetypes.guess_type(str(image_path))
        if mime_type is None:
            mime_type = "application/octet-stream"

        with open(image_path, "rb") as f:
            data = f.read()

        # Inline display; browsers will render the image instead of prompting download
        headers = {"Content-Disposition": "inline"}
        return Response(content=data, media_type=mime_type, headers=headers)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve image.",
        ) from e


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)