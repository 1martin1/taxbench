import os
import subprocess
import tempfile
from typing import List

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

app = FastAPI(title="GIF Creator API", version="1.0.0")

# Limits to mitigate DoS attacks
MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # 5 MiB per image
MAX_IMAGE_COUNT = 20  # reasonable upper bound


def _parse_target_size(size_str: str) -> tuple[int, int]:
    """
    Validate that ``size_str`` is in the form ``WIDTHxHEIGHT`` where both
    dimensions are positive integers. Returns a tuple ``(width, height)``.
    """
    if "x" not in size_str:
        raise ValueError("targetSize must be in WIDTHxHEIGHT format.")
    w_str, h_str = size_str.split("x", 1)
    if not (w_str.isdigit() and h_str.isdigit()):
        raise ValueError("targetSize dimensions must be integers.")
    width, height = int(w_str), int(h_str)
    if width <= 0 or height <= 0:
        raise ValueError("targetSize dimensions must be positive.")
    return width, height


@app.post(
    "/create-gif",
    responses={
        200: {"content": {"image/gif": {}}},
        400: {"content": {"application/json": {}}},
        500: {"content": {"application/json": {}}},
    },
)
async def create_gif(
    images: List[UploadFile] = File(..., description="Array of images to be included in the GIF."),
    targetSize: str = Form(..., description="Target size for the GIF in pixels (width x height)."),
    delay: int = Form(10, description="Delay between frames in milliseconds."),
    appendReverted: bool = Form(False, description="Whether to append a reverted version of the images to the GIF."),
):
    # Validate number of images
    if not images:
        raise HTTPException(status_code=400, detail="No images were provided.")
    if len(images) > MAX_IMAGE_COUNT:
        raise HTTPException(
            status_code=400,
            detail=f"Number of images exceeds the allowed maximum of {MAX_IMAGE_COUNT}.",
        )

    # Validate targetSize
    try:
        _parse_target_size(targetSize)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    # Validate delay
    if delay < 0:
        raise HTTPException(status_code=400, detail="Delay must be non‑negative.")

    # Convert delay from milliseconds to ImageMagick centiseconds (1/100 sec)
    delay_cs = max(1, delay // 10)  # ImageMagick requires at least 1 centisecond

    # Process uploads in a temporary directory
    with tempfile.TemporaryDirectory() as tmpdir:
        input_paths: List[str] = []
        for idx, upload in enumerate(images):
            # Enforce per‑file size limit
            content = await upload.read()
            if len(content) > MAX_UPLOAD_SIZE:
                raise HTTPException(
                    status_code=400,
                    detail=f"Uploaded file '{upload.filename}' exceeds the size limit of {MAX_UPLOAD_SIZE // (1024 * 1024)} MiB.",
                )

            # Preserve original extension if possible, fallback to .png
            _, ext = os.path.splitext(upload.filename)
            ext = ext if ext else ".png"
            safe_ext = ext.lower()
            if safe_ext not in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff"}:
                # Restrict to common image extensions to avoid unexpected behaviour
                safe_ext = ".png"

            file_path = os.path.join(tmpdir, f"frame_{idx}{safe_ext}")

            try:
                with open(file_path, "wb") as f:
                    f.write(content)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to write uploaded image: {e}")

            input_paths.append(file_path)

        # Build frame list, optionally appending the reversed sequence
        frames = input_paths.copy()
        if appendReverted:
            frames += list(reversed(input_paths))

        output_path = os.path.join(tmpdir, "output.gif")

        # Build ImageMagick command safely (list arguments, no shell)
        cmd = [
            "convert",
            "-delay",
            str(delay_cs),
            "-loop",
            "0",
            "-resize",
            targetSize,
            *frames,
            output_path,
        ]

        try:
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=tmpdir,
            )
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.decode().strip() or "Unknown ImageMagick error."
            raise HTTPException(status_code=500, detail=f"ImageMagick failed: {err_msg}")

        # Read the generated GIF
        try:
            with open(output_path, "rb") as f:
                gif_bytes = f.read()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read generated GIF: {e}")

    return Response(content=gif_bytes, media_type="image/gif")


# Custom error handler to match the OpenAPI spec (error field)
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


# Convert FastAPI validation errors (422) into 400 responses with the expected schema
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Extract a simple message; in production you might want to format details.
    detail = "Invalid request parameters."
    return JSONResponse(status_code=400, content={"error": detail})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000, log_level="info")