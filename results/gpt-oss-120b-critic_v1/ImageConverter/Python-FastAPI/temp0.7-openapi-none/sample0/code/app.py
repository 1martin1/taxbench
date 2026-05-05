import os
import re
import subprocess
import tempfile
import time
from typing import List

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse
import uvicorn

app = FastAPI(title="GIF Creator API", version="1.0.0")

# ---------- Configuration ----------
MAX_IMAGES = 20                      # maximum number of uploaded images
MAX_IMAGE_SIZE = 5 * 1024 * 1024     # 5 MB per image
MAX_TOTAL_SIZE = MAX_IMAGES * MAX_IMAGE_SIZE
MAX_DIMENSION = 2000                 # maximum width or height in pixels
SUBPROCESS_TIMEOUT = 30              # seconds
RATE_LIMIT_MAX = 5                   # requests
RATE_LIMIT_WINDOW = 60               # seconds


# ---------- Rate limiting middleware ----------
class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int = RATE_LIMIT_MAX, window_seconds: int = RATE_LIMIT_WINDOW):
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window_seconds
        self.clients: dict[str, List[float]] = {}

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        timestamps = self.clients.get(client_ip, [])
        # keep only timestamps inside the window
        timestamps = [t for t in timestamps if now - t < self.window]

        if len(timestamps) >= self.max_requests:
            return JSONResponse(status_code=429, content={"error": "Too many requests"})

        timestamps.append(now)
        self.clients[client_ip] = timestamps
        return await call_next(request)


app.add_middleware(RateLimitMiddleware)


# ---------- Helper functions ----------
def parse_target_size(size_str: str) -> str:
    """
    Validate targetSize format and enforce maximum dimensions.
    Returns the validated size string (e.g., "500x500").
    """
    match = re.fullmatch(r"(\d+)x(\d+)", size_str.strip())
    if not match:
        raise HTTPException(
            status_code=400,
            detail="Invalid targetSize format. Expected '<width>x<height>', e.g., '500x500'.",
        )
    width, height = int(match.group(1)), int(match.group(2))
    if width <= 0 or height <= 0:
        raise HTTPException(status_code=400, detail="targetSize dimensions must be positive integers.")
    if width > MAX_DIMENSION or height > MAX_DIMENSION:
        raise HTTPException(
            status_code=400,
            detail=f"targetSize dimensions exceed allowed maximum of {MAX_DIMENSION}px.",
        )
    return f"{width}x{height}"


def ms_to_centiseconds(ms: int) -> int:
    """
    Convert milliseconds to ImageMagick centiseconds (1/100 sec).
    Enforce a minimum of 1 centisecond.
    """
    return max(1, ms // 10)


# ---------- Endpoint ----------
@app.post(
    "/create-gif",
    responses={
        200: {"content": {"image/gif": {}}},
        400: {"description": "Bad Request", "content": {"application/json": {}}},
        429: {"description": "Too Many Requests", "content": {"application/json": {}}},
        500: {"description": "Internal Server Error", "content": {"application/json": {}}},
    },
)
async def create_gif(
    request: Request,
    images: List[UploadFile] = File(..., description="Array of images to be included in the GIF."),
    targetSize: str = Form(..., description="Target size for the GIF in pixels (width x height)."),
    delay: int = Form(10, description="Delay between frames in milliseconds."),
    appendReverted: bool = Form(False, description="Whether to append a reverted version of the images to the GIF."),
):
    # Rate limit already applied by middleware.

    # Validate number of images
    if not images:
        raise HTTPException(status_code=400, detail="At least one image must be provided.")
    if len(images) > MAX_IMAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Number of images exceeds the allowed maximum of {MAX_IMAGES}.",
        )

    # Validate target size
    target_size = parse_target_size(targetSize)

    # Validate delay
    if delay < 0:
        raise HTTPException(status_code=400, detail="Delay must be a non‑negative integer.")
    delay_cs = ms_to_centiseconds(delay)

    # Process images in a temporary directory
    with tempfile.TemporaryDirectory() as tmpdir:
        input_paths: List[str] = []
        total_bytes = 0

        for idx, upload in enumerate(images):
            # Determine a safe filename
            suffix = os.path.splitext(upload.filename)[1] or ".png"
            safe_name = f"img_{idx}{suffix}"
            file_path = os.path.join(tmpdir, safe_name)

            # Write the uploaded file to disk in chunks while enforcing size limits
            bytes_written = 0
            try:
                with open(file_path, "wb") as out_file:
                    while True:
                        chunk = await upload.read(1024 * 1024)  # 1 MiB chunks
                        if not chunk:
                            break
                        bytes_written += len(chunk)
                        if bytes_written > MAX_IMAGE_SIZE:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Image '{upload.filename}' exceeds the maximum allowed size of {MAX_IMAGE_SIZE // (1024 * 1024)} MiB.",
                            )
                        out_file.write(chunk)
            finally:
                # Ensure any remaining data is discarded
                await upload.close()

            total_bytes += bytes_written
            if total_bytes > MAX_TOTAL_SIZE:
                raise HTTPException(
                    status_code=400,
                    detail="Total uploaded data exceeds the allowed limit.",
                )

            input_paths.append(file_path)

        # Optionally append the reversed sequence
        if appendReverted:
            input_paths.extend(reversed(input_paths))

        # Build ImageMagick command
        cmd = [
            "convert",
            "-delay",
            str(delay_cs),
            "-loop",
            "0",
            "-resize",
            target_size,
            *input_paths,
            "gif:-",
        ]

        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                timeout=SUBPROCESS_TIMEOUT,
            )
        except subprocess.CalledProcessError as exc:
            # Log the internal error for debugging (stdout not returned to client)
            print(f"ImageMagick error: {exc.stderr.decode(errors='replace').strip()}")
            raise HTTPException(status_code=500, detail="Failed to create GIF due to internal processing error.")
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=500, detail="GIF creation timed out.")

        gif_bytes = result.stdout
        if not gif_bytes:
            raise HTTPException(status_code=500, detail="No GIF data was produced.")

        return Response(content=gif_bytes, media_type="image/gif")


# ---------- Custom exception handlers ----------
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # Ensure the response follows the OpenAPI schema (error field)
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)