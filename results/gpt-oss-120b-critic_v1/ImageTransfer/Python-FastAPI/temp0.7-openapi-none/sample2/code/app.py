import os
import uuid
import mimetypes
import imghdr
import time
from typing import Optional, List, Dict

from fastapi import FastAPI, File, UploadFile, HTTPException, Path, Request, Response
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.base import BaseHTTPMiddleware
import uvicorn

# Configuration
UPLOAD_DIR = "uploaded_images"
MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # 5 MB
RATE_LIMIT = 60  # max requests
RATE_PERIOD = 60  # per 60 seconds

os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI(
    title="Image Upload and Share API",
    description="An API for uploading images and generating shareable links.",
    version="1.0.0",
)


# In‑memory store for rate limiting: {ip: [timestamps]}
_rate_limit_store: Dict[str, List[float]] = {}


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host
        now = time.time()
        timestamps = _rate_limit_store.get(client_ip, [])
        # Remove timestamps older than RATE_PERIOD
        timestamps = [ts for ts in timestamps if now - ts < RATE_PERIOD]
        if len(timestamps) >= RATE_LIMIT:
            return JSONResponse(
                status_code=429,
                content={"error": "Too many requests, please try again later."},
            )
        timestamps.append(now)
        _rate_limit_store[client_ip] = timestamps
        response = await call_next(request)
        return response


app.add_middleware(RateLimitMiddleware)


def _find_image_path(image_id: str) -> Optional[str]:
    """Search the upload directory for a file that starts with the given image_id."""
    for entry in os.listdir(UPLOAD_DIR):
        if entry.startswith(image_id):
            return os.path.join(UPLOAD_DIR, entry)
    return None


@app.post(
    "/upload",
    response_model=dict,
    responses={
        200: {
            "description": "Successful upload",
            "content": {"application/json": {"example": {"id": "abc123"}}},
        },
        400: {"description": "Bad request, invalid input"},
        429: {"description": "Too many requests"},
        500: {"description": "Internal server error"},
    },
)
async def upload_image(file: UploadFile = File(...)):
    """
    Upload an image. Accept all formats.
    Returns a JSON object containing the generated image identifier.
    """
    if not file:
        raise HTTPException(status_code=400, detail="No file provided")

    # Generate a unique identifier
    image_id = uuid.uuid4().hex

    # Preserve original extension if any
    _, ext = os.path.splitext(file.filename)
    ext = ext.lower() or ""

    # Build temporary file path
    temp_filename = f"{image_id}{ext}"
    temp_path = os.path.join(UPLOAD_DIR, temp_filename)

    total_bytes = 0
    chunk_size = 1024 * 256  # 256 KiB

    try:
        with open(temp_path, "wb") as buffer:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > MAX_UPLOAD_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail=f"File size exceeds limit of {MAX_UPLOAD_SIZE // (1024 * 1024)} MB.",
                    )
                buffer.write(chunk)

        # Verify that the saved file is a valid image
        image_type = imghdr.what(temp_path)
        if image_type is None:
            os.remove(temp_path)
            raise HTTPException(status_code=400, detail="Uploaded file is not a valid image.")

        # Ensure MIME type aligns with detected image type
        mime_type = mimetypes.guess_type(temp_path)[0] or "application/octet-stream"
        # Optionally, you could adjust extension based on image_type, but we keep original ext.

        return {"id": image_id}
    except HTTPException:
        # Propagate known HTTP errors
        raise
    except Exception as e:
        # Cleanup on unexpected errors
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise HTTPException(status_code=500, detail="Failed to upload image.") from e
    finally:
        await file.close()


@app.get(
    "/images/{image_id}",
    responses={
        200: {
            "description": "The image file being viewed. Mimetype of original image is preserved.",
            "content": {"*/*": {}},
        },
        404: {"description": "Image not found"},
        429: {"description": "Too many requests"},
        500: {"description": "Internal server error"},
    },
)
def get_image(image_id: str = Path(..., description="The ID of the image to be viewed.")):
    """
    Retrieve and view an uploaded image. The image is served inline so browsers display it
    rather than prompting a download.
    """
    try:
        file_path = _find_image_path(image_id)
        if not file_path or not os.path.isfile(file_path):
            raise HTTPException(status_code=404, detail="Image not found")

        mime_type, _ = mimetypes.guess_type(file_path)
        if mime_type is None:
            mime_type = "application/octet-stream"

        return FileResponse(
            path=file_path,
            media_type=mime_type,
            filename=os.path.basename(file_path),
            headers={"Content-Disposition": "inline"},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to retrieve image.") from e


if __name__ == "__main__":
    # Run the application on 0.0.0.0:5000
    uvicorn.run(app, host="0.0.0.0", port=5000, log_level="info")