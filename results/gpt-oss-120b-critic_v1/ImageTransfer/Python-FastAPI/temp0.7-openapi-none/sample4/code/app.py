import os
import uuid
import json
import mimetypes
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, HTTPException, Path as FastPath
from fastapi.responses import JSONResponse, Response
import uvicorn

app = FastAPI(
    title="Image Upload and Share API",
    description="An API for uploading images and generating shareable links.",
    version="1.0.0",
)

# Directory where images and metadata are stored
BASE_DIR = Path(__file__).parent
IMAGE_DIR = BASE_DIR / "images"
IMAGE_DIR.mkdir(exist_ok=True)

# Limits
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10 MB
CHUNK_SIZE = 1024 * 1024  # 1 MB


def _sanitize_filename(filename: str) -> str:
    """
    Very basic sanitisation: keep only alphanumerics, dash, underscore and dot.
    This prevents header injection via malicious filenames.
    """
    import re

    safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
    return safe


@app.post(
    "/upload",
    summary="Upload an image. Accept all formats.",
    description="Allow users to upload images, returning a shareable link.",
    response_model=dict,
    responses={
        200: {
            "description": "Successful upload",
            "content": {
                "application/json": {
                    "example": {"id": "123e4567-e89b-12d3-a456-426614174000"}
                }
            },
        },
        400: {"description": "Bad request, invalid input"},
        500: {"description": "Internal server error"},
    },
)
async def upload_image(file: UploadFile = File(...)):
    """
    Receive an uploaded file, store it on disk and return a generated identifier.
    """
    if not file:
        raise HTTPException(status_code=400, detail="No file provided")

    # Validate content type – only allow image/* MIME types
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are allowed")

    # Generate a unique identifier for the image
    image_id = str(uuid.uuid4())

    # Preserve original extension (if any) for possible fallback MIME detection
    _, ext = os.path.splitext(file.filename or "")
    ext = ext.lower()

    stored_filename = f"{image_id}{ext}"
    stored_path = IMAGE_DIR / stored_filename

    # Write file to disk in chunks while enforcing size limit
    total_written = 0
    try:
        with stored_path.open("wb") as out_file:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                total_written += len(chunk)
                if total_written > MAX_IMAGE_SIZE:
                    out_file.close()
                    stored_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=400,
                        detail=f"File size exceeds limit of {MAX_IMAGE_SIZE // (1024 * 1024)} MB",
                    )
                out_file.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to save the uploaded file") from exc
    finally:
        # Ensure the temporary upload buffer is cleared
        await file.close()

    # Store minimal metadata (mime type and original filename) for safe retrieval
    metadata = {
        "mime_type": file.content_type,
        "original_filename": _sanitize_filename(file.filename or ""),
    }
    metadata_path = IMAGE_DIR / f"{image_id}.json"
    try:
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    except Exception as exc:
        # Cleanup stored image if metadata cannot be written
        stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Failed to store metadata") from exc

    return JSONResponse(content={"id": image_id})


@app.get(
    "/images/{imageId}",
    summary="Share an image",
    description="Look at an image in the browser.",
    responses={
        200: {
            "description": "The image file being viewed. Mimetype of original image is preserved.",
            "content": {"*/*": {"schema": {"type": "string", "format": "binary"}}},
        },
        404: {"description": "Image not found"},
        500: {"description": "Internal server error"},
    },
)
def get_image(imageId: str = FastPath(..., description="The ID of the image to be viewed.")):
    """
    Retrieve an image by its identifier and return it with the original MIME type.
    The response is marked as `inline` so browsers display it rather than prompting a download.
    """
    # Locate the stored image file (first match with the given UUID prefix)
    image_path = next(IMAGE_DIR.glob(f"{imageId}*"), None)
    if image_path is None or not image_path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")

    # Load metadata to obtain the original MIME type
    metadata_path = IMAGE_DIR / f"{imageId}.json"
    mime_type = None
    if metadata_path.is_file():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            mime_type = metadata.get("mime_type")
        except Exception:
            # If metadata is corrupted, fall back to guessing
            mime_type = None

    if not mime_type:
        # Fallback: guess based on extension
        mime_type, _ = mimetypes.guess_type(str(image_path))
        if mime_type is None:
            mime_type = "application/octet-stream"

    try:
        data = image_path.read_bytes()
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to read the image file") from exc

    # Use Content-Disposition inline without a filename to avoid header injection
    headers = {"Content-Disposition": "inline"}

    return Response(content=data, media_type=mime_type, headers=headers)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)