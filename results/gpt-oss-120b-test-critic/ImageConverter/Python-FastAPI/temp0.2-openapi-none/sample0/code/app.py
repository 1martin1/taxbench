import os
import subprocess
import tempfile
from pathlib import Path
from typing import List, Tuple

import logging
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Response
from starlette.concurrency import run_in_threadpool

# Configuration constants
MAX_FILES = 20               # Maximum number of uploaded images
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB per image
CONVERT_TIMEOUT = 30        # Seconds

# Set up basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="GIF Creator API",
    description="API to create a GIF from a set of images with specified parameters.",
    version="1.0.0",
)


def _process_gif(
    files_data: List[Tuple[bytes, str]],
    width: int,
    height: int,
    delay: int,
    append_reverted: bool,
) -> bytes:
    """
    Blocking helper that writes temporary files, invokes ImageMagick's `convert`,
    and returns the generated GIF bytes.
    """
    temp_input_paths: List[str] = []
    out_path: str = ""

    try:
        # Write each uploaded image to a temporary file
        for content, suffix in files_data:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(content)
                temp_input_paths.append(tmp.name)

        # Build the ordered list of input files
        input_files = temp_input_paths.copy()
        if append_reverted:
            input_files += list(reversed(temp_input_paths))

        # Temporary file for the output GIF
        with tempfile.NamedTemporaryFile(delete=False, suffix=".gif") as out_tmp:
            out_path = out_tmp.name

        # ImageMagick delay is expressed in 1/100ths of a second
        im_delay = max(1, int(delay / 10))

        cmd = [
            "convert",
            "-delay",
            str(im_delay),
            "-loop",
            "0",
            "-resize",
            f"{width}x{height}!",
        ] + input_files + [out_path]

        logger.info("Running ImageMagick command: %s", " ".join(cmd))

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=CONVERT_TIMEOUT,
        )

        if result.returncode != 0:
            logger.error("ImageMagick error: %s", result.stderr.strip())
            raise RuntimeError("Failed to create GIF.")

        # Read the generated GIF
        with open(out_path, "rb") as f:
            gif_bytes = f.read()

        return gif_bytes

    finally:
        # Clean up temporary input files
        for path in temp_input_paths:
            try:
                os.unlink(path)
            except OSError:
                pass
        # Clean up the temporary output file
        if out_path:
            try:
                os.unlink(out_path)
            except OSError:
                pass


@app.post(
    "/create-gif",
    responses={
        200: {"content": {"image/gif": {}}},
        400: {"description": "Bad Request"},
        500: {"description": "Internal Server Error"},
    },
)
async def create_gif(
    images: List[UploadFile] = File(..., description="Array of images to be included in the GIF."),
    targetSize: str = Form(..., description="Target size for the GIF in pixels (width x height)."),
    delay: int = Form(10, description="Delay between frames in milliseconds."),
    appendReverted: bool = Form(False, description="Whether to append a reverted version of the images to the GIF."),
):
    """
    Create a GIF from uploaded images using ImageMagick.
    """
    # Enforce limits on number of files
    if len(images) == 0:
        raise HTTPException(status_code=400, detail="No images provided.")
    if len(images) > MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Too many images. Maximum allowed is {MAX_FILES}.",
        )

    # Validate targetSize format (e.g., "500x500")
    try:
        width_str, height_str = targetSize.lower().split("x")
        width = int(width_str)
        height = int(height_str)
        if width <= 0 or height <= 0:
            raise ValueError
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid targetSize format. Expected WIDTHxHEIGHT with positive integers.",
        )

    # Validate delay (must be non‑negative)
    if delay < 0:
        raise HTTPException(status_code=400, detail="Delay must be non‑negative.")

    # Read and validate each uploaded file
    files_data: List[Tuple[bytes, str]] = []
    for img in images:
        suffix = Path(img.filename).suffix or ".png"
        content = await img.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"File '{img.filename}' exceeds the maximum allowed size of {MAX_FILE_SIZE // (1024 * 1024)} MB.",
            )
        files_data.append((content, suffix))

    # Run the blocking processing in a thread pool to avoid blocking the event loop
    try:
        gif_bytes = await run_in_threadpool(
            _process_gif,
            files_data,
            width,
            height,
            delay,
            appendReverted,
        )
    except RuntimeError as exc:
        logger.exception("GIF creation failed.")
        raise HTTPException(status_code=500, detail="Failed to create GIF.") from exc
    except subprocess.TimeoutExpired:
        logger.exception("ImageMagick conversion timed out.")
        raise HTTPException(status_code=500, detail="GIF creation timed out.") from None
    except Exception:
        logger.exception("Unexpected error during GIF creation.")
        raise HTTPException(status_code=500, detail="Internal server error.") from None

    return Response(content=gif_bytes, media_type="image/gif")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)