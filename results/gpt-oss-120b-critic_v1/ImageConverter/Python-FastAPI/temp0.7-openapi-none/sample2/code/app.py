import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List

import anyio
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import JSONResponse, Response
import uvicorn

app = FastAPI(
    title="GIF Creator API",
    description="API to create a GIF from a set of images with specified parameters.",
    version="1.0.0",
)

# ---- Configuration limits ----
MAX_FILES = 20                     # maximum number of uploaded images
MAX_FILE_SIZE = 5 * 1024 * 1024    # 5 MB per image
MAX_TOTAL_SIZE = 20 * MAX_FILE_SIZE  # overall limit for all images together
MAX_CONCURRENT_REQUESTS = 5        # simple concurrency guard
CONVERT_TIMEOUT = 30               # seconds

# Simple semaphore to limit concurrent processing
request_semaphore = anyio.Semaphore(MAX_CONCURRENT_REQUESTS)


def parse_target_size(size_str: str) -> str:
    """Validate and return target size string (e.g., '500x500')."""
    if "x" not in size_str:
        raise ValueError("targetSize must be in the format WIDTHxHEIGHT")
    w, h = size_str.lower().split("x")
    if not (w.isdigit() and h.isdigit()):
        raise ValueError("targetSize must contain numeric width and height")
    return f"{int(w)}x{int(h)}"


def check_convert_available() -> None:
    """Ensure ImageMagick's convert binary is present."""
    if shutil.which("convert") is None:
        raise RuntimeError("ImageMagick 'convert' binary not found in PATH.")


@app.on_event("startup")
async def startup_event():
    """Validate external dependencies at startup."""
    try:
        check_convert_available()
    except RuntimeError as exc:
        # Fail fast – the application cannot serve requests without convert.
        raise RuntimeError(f"Startup validation failed: {exc}") from exc


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
    request: Request,
    images: List[UploadFile] = File(..., description="Array of images to be included in the GIF."),
    targetSize: str = Form(..., description="Target size for the GIF in pixels (width x height)."),
    delay: int = Form(10, description="Delay between frames in milliseconds."),
    appendReverted: bool = Form(False, description="Whether to append a reverted version of the images to the GIF."),
):
    # ---- Basic validation ----
    if len(images) == 0:
        raise HTTPException(status_code=400, detail="At least one image must be provided.")
    if len(images) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Number of images exceeds the allowed maximum of {MAX_FILES}.")

    try:
        target_size_valid = parse_target_size(targetSize)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if delay < 0:
        raise HTTPException(status_code=400, detail="Delay must be non‑negative")
    # ImageMagick delay is expressed in 1/100 seconds (ticks)
    delay_ticks = max(1, int(delay / 10))

    # ---- Concurrency guard ----
    async with request_semaphore:
        # ---- Process files ----
        total_bytes = 0
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = Path(tmpdir)
                input_paths: List[Path] = []

                for idx, upload in enumerate(images):
                    # Determine a safe extension; default to .png if missing
                    ext = Path(upload.filename).suffix or ".png"
                    safe_name = f"img_{idx}{ext}"
                    dest_path = tmp_path / safe_name

                    # Stream upload to disk while enforcing size limits
                    file_bytes = 0
                    with dest_path.open("wb") as out_file:
                        while True:
                            chunk = await upload.read(1024 * 1024)  # 1 MiB chunks
                            if not chunk:
                                break
                            chunk_len = len(chunk)
                            file_bytes += chunk_len
                            total_bytes += chunk_len

                            if file_bytes > MAX_FILE_SIZE:
                                raise HTTPException(
                                    status_code=400,
                                    detail=f"File '{upload.filename}' exceeds per‑file size limit of {MAX_FILE_SIZE // (1024 * 1024)} MiB."
                                )
                            if total_bytes > MAX_TOTAL_SIZE:
                                raise HTTPException(
                                    status_code=400,
                                    detail=f"Total uploaded payload exceeds limit of {MAX_TOTAL_SIZE // (1024 * 1024)} MiB."
                                )
                            out_file.write(chunk)

                    input_paths.append(dest_path)

                # Optionally append reversed sequence
                if appendReverted:
                    input_paths.extend(reversed(input_paths))

                output_path = tmp_path / "output.gif"

                # Build ImageMagick command safely
                cmd = [
                    "convert",
                    "-delay",
                    str(delay_ticks),
                    "-loop",
                    "0",
                    *[str(p) for p in input_paths],
                    "-resize",
                    target_size_valid,
                    str(output_path),
                ]

                # Execute with timeout to avoid hanging
                try:
                    subprocess.run(
                        cmd,
                        check=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=CONVERT_TIMEOUT,
                    )
                except subprocess.TimeoutExpired:
                    raise HTTPException(status_code=500, detail="Image conversion timed out.")
                except subprocess.CalledProcessError as e:
                    err_msg = e.stderr.decode(errors="ignore") if e.stderr else "Image conversion failed."
                    raise HTTPException(status_code=500, detail=err_msg)

                # Return the generated GIF
                gif_bytes = output_path.read_bytes()
                return Response(content=gif_bytes, media_type="image/gif")

        except HTTPException:
            # Re‑raise known HTTP errors unchanged
            raise
        except Exception as exc:
            # Logically unexpected errors are turned into a 500 response
            raise HTTPException(status_code=500, detail=str(exc))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)