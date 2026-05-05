import uuid
import json
import imghdr
import mimetypes
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, UploadFile, File, HTTPException, status
from fastapi.responses import JSONResponse, FileResponse

app = FastAPI(
    title="Image Upload and Share API",
    description="An API for uploading images and generating shareable links.",
    version="1.0.0",
)

# Configuration
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
INDEX_FILE = UPLOAD_DIR / "index.json"
MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # 5 MB
ALLOWED_IMAGE_TYPES = {
    "jpeg": ".jpg",
    "png": ".png",
    "gif": ".gif",
    "webp": ".webp",
    "bmp": ".bmp",
    "tiff": ".tiff",
    "svg": ".svg",
}

# Ensure upload directory exists
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Load or initialise the index mapping image_id -> file extension
def load_index() -> Dict[str, str]:
    if INDEX_FILE.is_file():
        try:
            return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        except Exception:
            # Corrupted index – start fresh
            return {}
    return {}

def save_index(index: Dict[str, str]) -> None:
    INDEX_FILE.write_text(json.dumps(index), encoding="utf-8")

image_index: Dict[str, str] = load_index()


def is_allowed_image_type(detected_type: str) -> bool:
    return detected_type in ALLOWED_IMAGE_TYPES


def safe_path(path: Path) -> Path:
    """
    Resolve the path and ensure it stays within the UPLOAD_DIR to prevent
    directory traversal or symlink attacks.
    """
    resolved = path.resolve()
    if not str(resolved).startswith(str(UPLOAD_DIR.resolve())):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Invalid file path."},
        )
    return resolved


@app.post("/upload", response_class=JSONResponse, status_code=status.HTTP_200_OK)
async def upload_image(file: UploadFile = File(...)):
    """
    Upload an image file and return a shareable identifier.
    """
    if not file:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "No file provided."},
        )

    # Read the file in chunks, enforce size limit
    content = bytearray()
    size = 0
    while True:
        chunk = await file.read(1024 * 1024)  # 1 MB chunks
        if not chunk:
            break
        size += len(chunk)
        if size > MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": f"File exceeds maximum allowed size of {MAX_UPLOAD_SIZE // (1024 * 1024)} MB."},
            )
        content.extend(chunk)

    await file.close()

    # Verify that the uploaded file is an image
    detected_type = imghdr.what(None, bytes(content))
    if not detected_type or not is_allowed_image_type(detected_type):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Uploaded file is not a supported image type."},
        )

    # Generate a unique identifier
    image_id = uuid.uuid4().hex
    extension = ALLOWED_IMAGE_TYPES[detected_type]
    stored_filename = f"{image_id}{extension}"
    stored_path = safe_path(UPLOAD_DIR / stored_filename)

    try:
        stored_path.write_bytes(content)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "Failed to save the uploaded image."},
        )

    # Update index
    image_index[image_id] = extension
    try:
        save_index(image_index)
    except Exception:
        # If index saving fails, clean up the stored file
        stored_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "Failed to record image metadata."},
        )

    return {"id": image_id}


@app.get("/images/{image_id}")
async def get_image(image_id: str):
    """
    Retrieve an uploaded image. The image is served inline so browsers display it.
    """
    extension = image_index.get(image_id)
    if not extension:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Image not found"},
        )

    stored_path = safe_path(UPLOAD_DIR / f"{image_id}{extension}")

    if not stored_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Image not found"},
        )

    mime_type, _ = mimetypes.guess_type(stored_path.name)
    if mime_type is None:
        mime_type = "application/octet-stream"

    headers = {
        "Content-Disposition": f'inline; filename="{stored_path.name}"'
    }

    return FileResponse(
        path=stored_path,
        media_type=mime_type,
        headers=headers,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)