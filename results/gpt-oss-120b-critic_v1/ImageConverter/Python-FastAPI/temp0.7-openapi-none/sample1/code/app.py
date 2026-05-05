import os
import re
import shutil
import subprocess
import tempfile
import uuid
from typing import List

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

app = FastAPI(
    title="GIF Creator API",
    description="API to create a GIF from a set of images with specified parameters.",
    version="1.0.0",
)

# ----- Configuration limits -----
MAX_IMAGES = 20                     # maximum number of images per request
MAX_FILE_SIZE = 5 * 1024 * 1024    # 5 MB per image
MAX_DIMENSION = 2000               # maximum width/height in pixels
CONVERT_TIMEOUT = 30               # seconds


def _parse_target_size(size_str: str) -> tuple[int, int]:
    """
    Validate the targetSize string and return (width, height) as integers.
    Expected format: WIDTHxHEIGHT (e.g., 500x500)
    """
    match = re.fullmatch(r"\s*(\d+)\s*x\s*(\d+)\s*", size_str)
    if not match:
        raise HTTPException(
            status_code=400,
            detail="Invalid targetSize format. Expected format WIDTHxHEIGHT, e.g., 500x500.",
        )
    width, height = map(int, match.groups())
    if width <= 0 or height <= 0:
        raise HTTPException(
            status_code=400,
            detail="targetSize dimensions must be positive integers.",
        )
    if width > MAX_DIMENSION or height > MAX_DIMENSION:
        raise HTTPException(
            status_code=400,
            detail=f"targetSize dimensions must not exceed {MAX_DIMENSION}px.",
        )
    return width, height


def _ms_to_cs(delay_ms: int) -> int:
    """
    Convert milliseconds to ImageMagick's delay unit (centiseconds, 1/100 second).
    Minimum delay is 1 centisecond.
    """
    cs = max(1, int(delay_ms / 10))
    return cs


def _sanitize_extension(filename: str) -> str:
    """
    Return a safe file extension (including the dot) based on the original filename.
    Only alphanumeric characters are allowed in the extension.
    """
    ext = os.path.splitext(filename)[1].lower()
    # Keep only letters, numbers and dot; default to .png if not safe
    if re.fullmatch(r"\.[a-z0-9]+", ext):
        return ext
    return ".png"


@app.post(
    "/create-gif",
    responses={
        200: {
            "description": "GIF created successfully",
            "content": {"image/gif": {"schema": {"type": "string", "format": "binary"}}},
        },
        400: {
            "description": "Bad Request",
            "content": {"application/json": {"schema": {"type": "object", "properties": {"error": {"type": "string"}}}}},
        },
        500: {
            "description": "Internal Server Error",
            "content": {"application/json": {"schema": {"type": "object", "properties": {"error": {"type": "string"}}}}},
        },
    },
)
async def create_gif(
    images: List[UploadFile] = File(..., description="Array of images to be included in the GIF."),
    targetSize: str = Form(..., description="Target size for the GIF in pixels (width x height)."),
    delay: int = Form(10, description="Delay between frames in milliseconds."),
    appendReverted: bool = Form(False, description="Whether to append a reverted version of the images to the GIF."),
):
    """
    Create a GIF from the uploaded images according to supplied parameters.
    """
    # ----- Validate request limits -----
    if len(images) == 0:
        raise HTTPException(status_code=400, detail="At least one image must be uploaded.")
    if len(images) > MAX_IMAGES:
        raise HTTPException(status_code=400, detail=f"Number of images must not exceed {MAX_IMAGES}.")

    if delay < 0:
        raise HTTPException(status_code=400, detail="Delay must be a non‑negative integer.")
    delay_cs = _ms_to_cs(delay)

    width, height = _parse_target_size(targetSize)
    target_size_str = f"{width}x{height}"

    # Ensure ImageMagick's convert binary exists
    convert_path = shutil.which("convert")
    if not convert_path:
        raise HTTPException(status_code=500, detail="Image processing tool not available on the server.")

    # ----- Process images -----
    with tempfile.TemporaryDirectory() as tmpdir:
        saved_paths: List[str] = []

        for idx, upload in enumerate(images):
            # Read file content with size guard
            content = await upload.read()
            if len(content) > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=400,
                    detail=f"Uploaded file '{upload.filename}' exceeds size limit of {MAX_FILE_SIZE // (1024 * 1024)} MB.",
                )

            # Generate a safe filename
            safe_ext = _sanitize_extension(upload.filename)
            safe_name = f"frame_{idx}_{uuid.uuid4().hex}{safe_ext}"
            file_path = os.path.join(tmpdir, safe_name)

            # Write file to disk in a thread pool to avoid blocking the event loop
            try:
                await run_in_threadpool(lambda: open(file_path, "wb").write(content))
            except Exception as exc:
                raise HTTPException(status_code=500, detail="Failed to store uploaded image.") from exc

            saved_paths.append(file_path)

        # Build frame sequence, optionally appending reversed order
        frame_paths = saved_paths.copy()
        if appendReverted:
            frame_paths.extend(reversed(saved_paths))

        output_gif_path = os.path.join(tmpdir, "output.gif")

        # Construct the ImageMagick command
        command = [
            convert_path,
            "-delay",
            str(delay_cs),
            "-loop",
            "0",
            "-resize",
            target_size_str,
            *frame_paths,
            output_gif_path,
        ]

        # Execute the command with timeout and error handling
        try:
            subprocess.run(
                command,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=CONVERT_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=500, detail="Image processing timed out.")
        except subprocess.CalledProcessError:
            # Do not expose internal ImageMagick errors
            raise HTTPException(status_code=500, detail="Failed to generate GIF from images.")
        except OSError as exc:
            # Handles cases where the binary cannot be executed
            raise HTTPException(status_code=500, detail="Image processing tool execution failed.") from exc

        # Read the generated GIF (also off the event loop)
        try:
            gif_bytes = await run_in_threadpool(lambda: open(output_gif_path, "rb").read())
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Failed to read generated GIF.") from exc

        return Response(content=gif_bytes, media_type="image/gif")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """
    Return error responses matching the OpenAPI error schema.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail if isinstance(exc.detail, str) else str(exc.detail)},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)