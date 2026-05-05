import uuid
import mimetypes
from pathlib import Path

from fastapi import (
    FastAPI,
    File,
    UploadFile,
    HTTPException,
    Request,
    Path as FastAPIPath,
)
from fastapi.responses import JSONResponse, FileResponse
import uvicorn

app = FastAPI(
    title="Image Upload and Share API",
    description="An API for uploading images and generating shareable links.",
    version="1.0.0",
)

# Directory where images are stored
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# Security / limits
MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # 5 MiB
ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/tiff",
    "image/svg+xml",
    "image/x-icon",
}
ALLOWED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
    ".tiff",
    ".svg",
    ".ico",
}


def _generate_image_id() -> str:
    """Create a random 32‑character hex identifier."""
    return uuid.uuid4().hex


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    """Return errors in the `{\"error\": \"msg\"}` shape required by the spec."""
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.exception_handler(Exception)
async def _generic_exception_handler(request: Request, exc: Exception):
    """Catch‑all handler for unexpected server errors."""
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


@app.post(
    "/upload",
    summary="Upload an image. Accept all formats.",
    description="Allow users to upload images, returning a shareable link.",
)
async def upload_image(file: UploadFile = File(...)):
    """Validate, store the uploaded image and return its identifier."""
    if not file:
        raise HTTPException(status_code=400, detail="No file provided")

    # Validate MIME type
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported file type")

    # Determine a safe file extension
    original_suffix = Path(file.filename).suffix.lower()
    if original_suffix in ALLOWED_EXTENSIONS:
        suffix = original_suffix
    else:
        guessed = mimetypes.guess_extension(file.content_type)
        suffix = guessed if guessed in ALLOWED_EXTENSIONS else ""

    image_id = _generate_image_id()
    dest_path = UPLOAD_DIR / f"{image_id}{suffix}"

    total_written = 0
    try:
        with dest_path.open("wb") as buffer:
            while True:
                chunk = await file.read(1024 * 1024)  # 1 MiB chunks
                if not chunk:
                    break
                total_written += len(chunk)
                if total_written > MAX_UPLOAD_SIZE:
                    dest_path.unlink(missing_ok=True)
                    raise HTTPException(status_code=400, detail="File too large")
                buffer.write(chunk)
    except HTTPException:
        raise
    except Exception:
        dest_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Failed to save file")
    finally:
        await file.close()

    return {"id": image_id}


@app.get(
    "/images/{imageId}",
    summary="Share an image",
    description="Look at an image in the browser.",
)
def get_image(
    imageId: str = FastAPIPath(..., regex="^[a-f0-9]{32}$")
):
    """Return the stored image, preserving its original MIME type."""
    matching_files = list(UPLOAD_DIR.glob(f"{imageId}.*"))
    if not matching_files:
        raise HTTPException(status_code=404, detail="Image not found")

    image_path = matching_files[0]
    mime_type, _ = mimetypes.guess_type(str(image_path))
    if mime_type is None:
        mime_type = "application/octet-stream"

    # Instruct browsers to display inline rather than download
    headers = {"Content-Disposition": f'inline; filename="{image_path.name}"'}

    return FileResponse(path=image_path, media_type=mime_type, headers=headers)


if __name__ == "__main__":
    # Run the API on 0.0.0.0:5000
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)