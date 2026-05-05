import os
import re
import uuid
import imghdr
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
    Request,
)
from fastapi.responses import JSONResponse

app = FastAPI(title="GIF Creator API", version="1.0.0")

# ---------- Configuration ----------
MAX_IMAGES = 20                 # Maximum number of images per request
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB per image
CHUNK_SIZE = 8192               # Chunk size for streaming uploads
SUBPROCESS_TIMEOUT = 30         # Seconds
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

# ---------- Utility functions ----------
def parse_target_size(size_str: str) -> tuple[int, int]:
    """
    Parse a size string formatted as WIDTHxHEIGHT into a tuple of ints.
    """
    match = re.fullmatch(r"\s*(\d+)\s*x\s*(\d+)\s*", size_str)
    if not match:
        raise ValueError(
            "targetSize must be in the format '<width>x<height>', e.g., '500x500'."
        )
    width, height = map(int, match.groups())
    if width <= 0 or height <= 0:
        raise ValueError("Width and height must be positive integers.")
    return width, height


def safe_filename(original: str) -> str:
    """
    Generate a safe filename using a UUID and preserve a allowed extension.
    """
    _, ext = os.path.splitext(original)
    ext = ext.lower()
    if ext not in ALLOWED_EXTENSIONS:
        ext = ".img"  # fallback generic extension
    return f"{uuid.uuid4().hex}{ext}"


def validate_image_file(path: str) -> None:
    """
    Basic validation that the file at `path` is an image using imghdr.
    Raises HTTPException if validation fails.
    """
    kind = imghdr.what(path)
    if kind is None:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is not a recognized image format.",
        )


def run_subprocess(cmd: List[str]) -> None:
    """
    Run a subprocess command with a timeout and uniform error handling.
    Raises RuntimeError on failure.
    """
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=SUBPROCESS_TIMEOUT,
            check=False,
            text=True,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "ImageMagick 'convert' executable not found. Ensure it is installed."
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Image processing timed out.")
    if result.returncode != 0:
        raise RuntimeError(
            f"ImageMagick command failed: {result.stderr.strip() or result.stdout}"
        )


def convert_images_to_gif(
    image_paths: List[str],
    output_path: str,
    delay_ms: int,
    loop: int = 0,
) -> None:
    """
    Use ImageMagick's `convert` to create a GIF from a list of image files.
    ImageMagick's -delay expects centiseconds (1/100 sec), so we convert.
    """
    delay_cs = max(1, int(delay_ms / 10))
    cmd = ["convert", "-delay", str(delay_cs), "-loop", str(loop)] + image_paths + [
        output_path
    ]
    run_subprocess(cmd)


# ---------- Endpoint ----------
@app.post(
    "/create-gif",
    responses={
        200: {"content": {"image/gif": {}}},
        400: {"description": "Bad Request", "content": {"application/json": {}}},
        500: {"description": "Internal Server Error", "content": {"application/json": {}}},
    },
)
async def create_gif(
    images: List[UploadFile] = File(..., description="Array of images to be included in the GIF."),
    targetSize: str = Form(..., description="Target size for the GIF in pixels (width x height)."),
    delay: int = Form(10, description="Delay between frames in milliseconds.", ge=0),
    appendReverted: bool = Form(False, description="Whether to append a reverted version of the images to the GIF."),
):
    # ---------- Input validation ----------
    if len(images) == 0:
        raise HTTPException(status_code=400, detail="At least one image must be provided.")
    if len(images) > MAX_IMAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Number of images exceeds the allowed maximum of {MAX_IMAGES}.",
        )
    try:
        width, height = parse_target_size(targetSize)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # ---------- Process in a temporary directory ----------
    with tempfile.TemporaryDirectory() as tmpdir:
        resized_paths: List[str] = []

        for idx, upload in enumerate(images):
            # Basic MIME type check
            if not upload.content_type or not upload.content_type.startswith("image/"):
                raise HTTPException(
                    status_code=400,
                    detail=f"File #{idx + 1} does not appear to be an image (content-type: {upload.content_type}).",
                )

            # Generate safe filenames
            original_name = safe_filename(upload.filename or f"image_{idx}")
            original_path = os.path.join(tmpdir, f"orig_{idx}_{original_name}")

            # Stream upload to disk while enforcing size limit
            total_written = 0
            try:
                with open(original_path, "wb") as dest_file:
                    while True:
                        chunk = await upload.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        total_written += len(chunk)
                        if total_written > MAX_FILE_SIZE:
                            raise HTTPException(
                                status_code=400,
                                detail=f"File #{idx + 1} exceeds the maximum allowed size of {MAX_FILE_SIZE // (1024 * 1024)} MiB.",
                            )
                        dest_file.write(chunk)
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to write uploaded file #{idx + 1}: {e}",
                )
            finally:
                # Ensure the upload's internal buffer is cleared
                await upload.close()

            # Verify the file is an image
            validate_image_file(original_path)

            # Resize image
            resized_name = safe_filename(f"resized_{idx}_{upload.filename or ''}")
            resized_path = os.path.join(tmpdir, f"resized_{idx}_{resized_name}")
            resize_cmd = [
                "convert",
                original_path,
                "-resize",
                f"{width}x{height}",
                resized_path,
            ]
            try:
                run_subprocess(resize_cmd)
            except RuntimeError as e:
                raise HTTPException(status_code=500, detail=str(e))

            resized_paths.append(resized_path)

        # Build final sequence
        final_sequence = resized_paths.copy()
        if appendReverted:
            final_sequence.extend(reversed(resized_paths))

        # Create GIF
        output_gif_path = os.path.join(tmpdir, "output.gif")
        try:
            convert_images_to_gif(
                image_paths=final_sequence,
                output_path=output_gif_path,
                delay_ms=delay,
                loop=0,
            )
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e))

        # Read GIF bytes
        try:
            with open(output_gif_path, "rb") as gif_file:
                gif_bytes = gif_file.read()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read generated GIF: {e}")

    return Response(content=gif_bytes, media_type="image/gif")


# ---------- Exception handlers ----------
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    # Log the exception details in a real application
    return JSONResponse(
        status_code=500, content={"error": "Internal server error."}
    )


# ---------- Application entry point ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)