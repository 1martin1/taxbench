import asyncio
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import List

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

# --------------------------------------------------------------------------- #
# Configuration & Limits
# --------------------------------------------------------------------------- #
MAX_IMAGES = 20                     # Maximum number of images per request
MAX_IMAGE_SIZE = 5 * 1024 * 1024    # 5 MB per image
MAX_TOTAL_SIZE = 20 * 1024 * 1024   # 20 MB total payload
CMD_TIMEOUT = 30                    # Seconds

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# FastAPI app
# --------------------------------------------------------------------------- #
app = FastAPI(
    title="GIF Creator API",
    description="API to create a GIF from a set of images with specified parameters.",
    version="1.0.0",
)


def _run_cmd(cmd: List[str]) -> None:
    """
    Execute a shell command with a timeout.
    Raises RuntimeError with a generic message on failure.
    """
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
            timeout=CMD_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Image processing timed out.")
    except Exception as exc:
        raise RuntimeError(f"Failed to execute command: {exc}")

    if result.returncode != 0:
        # Do not expose stderr to the client; log it instead.
        logger.error("Command failed: %s\nStderr: %s", " ".join(cmd), result.stderr.strip())
        raise RuntimeError("Image processing failed.")


@app.post("/create-gif")
async def create_gif(
    images: List[UploadFile] = File(..., description="Array of images to be included in the GIF."),
    targetSize: str = Form(..., description="Target size for the GIF in pixels (width x height)."),
    delay: int = Form(10, description="Delay between frames in milliseconds."),
    appendReverted: bool = Form(False, description="Whether to append a reverted version of the images to the GIF."),
):
    # ------------------------------------------------------------------- #
    # Validate request size limits
    # ------------------------------------------------------------------- #
    if len(images) == 0:
        raise HTTPException(status_code=400, detail="At least one image must be provided.")
    if len(images) > MAX_IMAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Too many images. Maximum allowed is {MAX_IMAGES}.",
        )

    total_payload = 0
    image_contents: List[bytes] = []
    for upload in images:
        content = await upload.read()
        size = len(content)
        if size > MAX_IMAGE_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"Image '{upload.filename}' exceeds the maximum size of {MAX_IMAGE_SIZE // (1024 * 1024)} MB.",
            )
        total_payload += size
        if total_payload > MAX_TOTAL_SIZE:
            raise HTTPException(
                status_code=400,
                detail="Total uploaded data exceeds the allowed limit.",
            )
        image_contents.append(content)

    # ------------------------------------------------------------------- #
    # Validate targetSize format and positivity
    # ------------------------------------------------------------------- #
    try:
        width_str, height_str = targetSize.lower().split("x")
        width = int(width_str)
        height = int(height_str)
        if width <= 0 or height <= 0:
            raise ValueError
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid targetSize format. Expected positive WIDTHxHEIGHT, e.g., 500x500.",
        )

    # Convert delay from milliseconds to ImageMagick centiseconds (1/100 sec)
    delay_cs = max(1, int(round(delay / 10.0)))

    # ------------------------------------------------------------------- #
    # Process images
    # ------------------------------------------------------------------- #
    temp_files: List[Path] = []
    try:
        # Step 1: Save, resize each image
        resized_paths: List[Path] = []
        for idx, (upload, content) in enumerate(zip(images, image_contents)):
            suffix = Path(upload.filename).suffix or ".png"

            # Save original upload to temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_orig:
                tmp_orig.write(content)
                tmp_orig_path = Path(tmp_orig.name)
                temp_files.append(tmp_orig_path)

            # Prepare resized temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_resized:
                tmp_resized_path = Path(tmp_resized.name)
                temp_files.append(tmp_resized_path)

            resize_cmd = [
                "convert",
                str(tmp_orig_path),
                "-resize",
                f"{width}x{height}",
                str(tmp_resized_path),
            ]

            await asyncio.to_thread(_run_cmd, resize_cmd)
            resized_paths.append(tmp_resized_path)

        # Step 2: Build frame list (optionally append reversed sequence)
        frame_paths = resized_paths.copy()
        if appendReverted:
            frame_paths.extend(reversed(resized_paths))

        # Step 3: Create GIF
        with tempfile.NamedTemporaryFile(delete=False, suffix=".gif") as tmp_gif:
            gif_path = Path(tmp_gif.name)
            temp_files.append(gif_path)

        gif_cmd = ["convert", "-delay", str(delay_cs), "-loop", "0"]
        gif_cmd.extend(str(p) for p in frame_paths)
        gif_cmd.append(str(gif_path))

        await asyncio.to_thread(_run_cmd, gif_cmd)

        # Step 4: Return GIF content
        gif_bytes = gif_path.read_bytes()
        return Response(content=gif_bytes, media_type="image/gif")

    except RuntimeError as exc:
        logger.exception("Processing error: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to generate GIF.")
    finally:
        # Cleanup temporary files
        for p in temp_files:
            try:
                if p.is_file():
                    p.unlink()
            except Exception:
                logger.warning("Failed to delete temporary file: %s", p)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)