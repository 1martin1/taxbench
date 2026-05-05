import os
import subprocess
import tempfile
import logging
from typing import List

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import JSONResponse, Response

# -------------------------------------------------------------------------
# Configuration & Limits
# -------------------------------------------------------------------------
MAX_FILES = 20               # Maximum number of uploaded images
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB per image
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

# -------------------------------------------------------------------------
# Logging Setup
# -------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------
# FastAPI Application
# -------------------------------------------------------------------------
app = FastAPI()


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Return JSON error for HTTPException without leaking internal details."""
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Catch‑all handler that logs the exception and returns a generic error."""
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


def _validate_target_size(target_size: str) -> tuple[int, int]:
    """Parse and validate the targetSize string (e.g., '500x500')."""
    if "x" not in target_size.lower():
        raise HTTPException(
            status_code=400,
            detail="Invalid targetSize format. Expected WIDTHxHEIGHT.",
        )
    try:
        width_str, height_str = target_size.lower().split("x")
        width = int(width_str)
        height = int(height_str)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid targetSize numbers.",
        )
    if width <= 0 or height <= 0:
        raise HTTPException(
            status_code=400,
            detail="Width and height must be positive integers.",
        )
    return width, height


def _validate_and_read_upload(upload: UploadFile, idx: int) -> bytes:
    """Read upload content while enforcing size limits and safe extensions."""
    # Enforce a reasonable size limit
    content = await upload.read()
    if len(content) == 0:
        raise HTTPException(
            status_code=400,
            detail=f"File {upload.filename!r} is empty.",
        )
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File {upload.filename!r} exceeds the maximum allowed size of {MAX_FILE_SIZE // (1024 * 1024)} MB.",
        )
    # Validate extension (optional, but helps avoid unexpected formats)
    _, ext = os.path.splitext(upload.filename)
    if ext.lower() not in ALLOWED_IMAGE_EXTENSIONS:
        # Default to .png if extension is unknown; we still accept the data.
        ext = ".png"
    return content, ext


@app.post("/create-gif")
async def create_gif(
    images: List[UploadFile] = File(..., description="Array of images to be included in the GIF."),
    targetSize: str = Form(..., description="Target size for the GIF in pixels (width x height)."),
    delay: int = Form(10, description="Delay between frames in milliseconds."),
    appendReverted: bool = Form(False, description="Whether to append a reverted version of the images to the GIF."),
):
    """
    Create a GIF from uploaded images using ImageMagick.
    """
    # -----------------------------------------------------------------
    # Basic parameter validation
    # -----------------------------------------------------------------
    if len(images) == 0:
        raise HTTPException(status_code=400, detail="No images provided.")
    if len(images) > MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Too many images uploaded. Maximum allowed is {MAX_FILES}.",
        )
    if delay < 0:
        raise HTTPException(status_code=400, detail="Delay must be non‑negative.")
    # ImageMagick expects delay in centiseconds (1/100 sec)
    delay_cs = max(1, int(delay / 10))

    width, height = _validate_target_size(targetSize)

    # -----------------------------------------------------------------
    # Process uploads in a temporary directory
    # -----------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmpdir:
        saved_paths: List[str] = []

        for idx, upload in enumerate(images):
            # Read and validate file content
            content = await upload.read()
            if len(content) == 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"File {upload.filename!r} is empty.",
                )
            if len(content) > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=400,
                    detail=f"File {upload.filename!r} exceeds the maximum allowed size of {MAX_FILE_SIZE // (1024 * 1024)} MB.",
                )
            # Determine a safe extension
            _, ext = os.path.splitext(upload.filename)
            if ext.lower() not in ALLOWED_IMAGE_EXTENSIONS:
                ext = ".png"
            safe_name = f"frame_{idx}{ext}"
            file_path = os.path.join(tmpdir, safe_name)

            # Write file to disk
            with open(file_path, "wb") as f:
                f.write(content)

            saved_paths.append(file_path)

        # Optionally append the reversed sequence
        if appendReverted:
            saved_paths += list(reversed(saved_paths))

        output_path = os.path.join(tmpdir, "result.gif")

        # -----------------------------------------------------------------
        # Build and execute ImageMagick command
        # -----------------------------------------------------------------
        cmd = [
            "convert",
            "-delay",
            str(delay_cs),
            "-loop",
            "0",
        ] + saved_paths + [
            "-resize",
            f"{width}x{height}!",
            output_path,
        ]

        try:
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.decode().strip() or "Unknown ImageMagick error."
            logger.error("ImageMagick failed: %s", err_msg)
            raise HTTPException(
                status_code=500,
                detail="Failed to generate GIF.",
            )
        except OSError as e:
            # This captures FileNotFoundError when 'convert' is missing
            logger.error("ImageMagick executable not found: %s", e)
            raise HTTPException(
                status_code=500,
                detail="Image processing tool is unavailable.",
            )

        # -----------------------------------------------------------------
        # Return the generated GIF
        # -----------------------------------------------------------------
        try:
            with open(output_path, "rb") as f:
                gif_bytes = f.read()
        except Exception as e:
            logger.exception("Failed to read generated GIF: %s", e)
            raise HTTPException(
                status_code=500,
                detail="Failed to read generated GIF.",
            )

    return Response(content=gif_bytes, media_type="image/gif")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)