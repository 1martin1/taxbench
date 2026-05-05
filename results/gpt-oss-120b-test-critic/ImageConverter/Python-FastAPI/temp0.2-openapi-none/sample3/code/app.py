import os
import shutil
import subprocess
import tempfile
from typing import List

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    Response,
    status,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(
    title="GIF Creator API",
    description="API to create a GIF from a set of images with specified parameters.",
    version="1.0.0",
)


# ---------- Configuration ----------
MAX_IMAGES = 20               # maximum number of images per request
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB per image
ALLOWED_CONTENT_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/gif",
}
# -----------------------------------


class ErrorResponse(BaseModel):
    error: str


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    """Return errors with an `error` field as defined in the OpenAPI spec."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request, exc: Exception):
    """Catch‑all handler for unexpected errors."""
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "Internal server error"},
    )


def parse_target_size(size_str: str):
    """
    Parse a size string like '500x500' into (width, height).
    """
    try:
        width_str, height_str = size_str.lower().split("x")
        width = int(width_str.strip())
        height = int(height_str.strip())
        if width <= 0 or height <= 0:
            raise ValueError
        return width, height
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid targetSize format. Expected format 'WIDTHxHEIGHT', e.g., '500x500'.",
        )


def ms_to_delay_units(delay_ms: int):
    """
    ImageMagick's -delay expects units of 1/100 second.
    Convert milliseconds to that unit (10 ms = 1 unit).
    """
    if delay_ms < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Delay must be a non‑negative integer.",
        )
    # Ensure at least 1 unit (10 ms) to avoid a zero delay which ImageMagick treats as instant.
    return max(1, delay_ms // 10)


def _run_convert_cmd(cmd: List[str], error_message: str):
    """
    Execute an ImageMagick `convert` command safely.
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ImageMagick `convert` binary not found on server.",
        )
    if result.returncode != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{error_message}: {result.stderr.strip()}",
        )
    return result


@app.post(
    "/create-gif",
    responses={
        200: {"content": {"image/gif": {}}},
        400: {"model": ErrorResponse, "description": "Bad Request"},
        500: {"model": ErrorResponse, "description": "Internal Server Error"},
    },
)
async def create_gif(
    images: List[UploadFile] = File(
        ..., description="Array of images to be included in the GIF."
    ),
    targetSize: str = Form(
        ..., description="Target size for the GIF in pixels (width x height)."
    ),
    delay: int = Form(10, description="Delay between frames in milliseconds."),
    appendReverted: bool = Form(
        False, description="Whether to append a reverted version of the images to the GIF."
    ),
):
    # ---------- Input validation ----------
    if not images:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one image must be provided.",
        )
    if len(images) > MAX_IMAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Too many images. Maximum allowed is {MAX_IMAGES}.",
        )

    for upload in images:
        if upload.content_type not in ALLOWED_CONTENT_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported file type: {upload.content_type}.",
            )
        # Read the file to enforce size limit (fastapi reads lazily)
        content = await upload.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File {upload.filename} exceeds size limit of {MAX_FILE_SIZE // (1024 * 1024)} MB.",
            )
        # Reset the file pointer for later use
        upload.file.seek(0)

    width, height = parse_target_size(targetSize)
    delay_units = ms_to_delay_units(delay)

    # ---------- Processing ----------
    work_dir = tempfile.mkdtemp()
    try:
        resized_paths = []

        for idx, upload in enumerate(images):
            # Save original upload to a temporary file
            orig_fd, original_path = tempfile.mkstemp(dir=work_dir, prefix=f"orig_{idx}_")
            os.close(orig_fd)  # We'll write using a file object
            with open(original_path, "wb") as f:
                # Re‑read the content (already validated)
                content = await upload.read()
                f.write(content)

            # Prepare resized file path (PNG for consistency)
            resized_path = os.path.join(work_dir, f"resized_{idx}.png")
            resize_cmd = [
                "convert",
                "-quiet",
                original_path,
                "-resize",
                f"{width}x{height}!",
                resized_path,
            ]
            _run_convert_cmd(resize_cmd, "Image resizing failed")

            resized_paths.append(resized_path)

        # Optionally append reversed sequence
        final_sequence = resized_paths.copy()
        if appendReverted:
            final_sequence += list(reversed(resized_paths))

        output_gif_path = os.path.join(work_dir, "output.gif")
        gif_cmd = [
            "convert",
            "-quiet",
            "-delay",
            str(delay_units),
            "-loop",
            "0",
        ] + final_sequence + [output_gif_path]

        _run_convert_cmd(gif_cmd, "GIF creation failed")

        # Return the GIF bytes
        with open(output_gif_path, "rb") as f:
            gif_bytes = f.read()

        return Response(content=gif_bytes, media_type="image/gif")
    finally:
        # Clean up all temporary files and directories
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)