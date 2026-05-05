import os
import shutil
import subprocess
import tempfile
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Response, status
from fastapi.responses import JSONResponse

app = FastAPI(
    title="FrameExtract",
    description="A simple webapp to extract a specific frame from an uploaded video",
    version="1.0.0",
)

# ----------------------------------------------------------------------
# Configuration / limits
# ----------------------------------------------------------------------
MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 MB
MAX_FRAME_NUMBER = 10_000_000       # Arbitrary upper bound to avoid abuse
FFMPEG_TIMEOUT = 15                 # seconds


def _copy_with_limit(src, dst_path: str, max_bytes: int) -> None:
    """Copy data from a file‑like object to a path, enforcing a size limit."""
    total = 0
    with open(dst_path, "wb") as dst:
        while True:
            chunk = src.read(8192)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Uploaded file exceeds the allowed size of {max_bytes // (1024 * 1024)} MiB.",
                )
            dst.write(chunk)


@app.post(
    "/extract",
    responses={
        200: {
            "description": "The extracted image if it was possible to extract the image",
            "content": {"image/png": {"schema": {"type": "string", "format": "binary"}}},
        },
        404: {
            "description": "Frame not found at the specified index.",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "error": {
                                "type": "string",
                                "example": "Frame at index 132 could not be found.",
                            }
                        },
                    }
                }
            },
        },
    },
)
async def extract(
    video: UploadFile = File(...),
    videoFileName: str = Form(..., description="Original filename of the video"),
    frameNumber: int = Form(..., description="Zero‑based index of the frame to extract"),
):
    """
    Extract a single frame from the uploaded video using ffmpeg.
    """
    # ------------------------------------------------------------------
    # Validate inputs
    # ------------------------------------------------------------------
    if frameNumber < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="frameNumber must be non‑negative",
        )
    if frameNumber > MAX_FRAME_NUMBER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"frameNumber exceeds the maximum allowed value of {MAX_FRAME_NUMBER}.",
        )

    # ------------------------------------------------------------------
    # Prepare temporary files
    # ------------------------------------------------------------------
    input_tmp_path: Optional[str] = None
    output_tmp_path: Optional[str] = None

    try:
        # Save uploaded video to a temporary file while enforcing size limit
        suffix = os.path.splitext(videoFileName)[1] or ".tmp"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as input_tmp:
            input_tmp_path = input_tmp.name
            _copy_with_limit(video.file, input_tmp_path, MAX_UPLOAD_SIZE)

        # Prepare temporary output file for the PNG image
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as output_tmp:
            output_tmp_path = output_tmp.name

        # ------------------------------------------------------------------
        # Build and run ffmpeg command
        # ------------------------------------------------------------------
        ffmpeg_cmd = [
            "ffmpeg",
            "-i",
            input_tmp_path,
            "-vf",
            f"select=eq(n\\,{frameNumber})",
            "-vframes",
            "1",
            "-f",
            "image2",
            output_tmp_path,
            "-hide_banner",
            "-loglevel",
            "error",
        ]

        result = subprocess.run(
            ffmpeg_cmd,
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT,
        )

        # ------------------------------------------------------------------
        # Handle ffmpeg outcome
        # ------------------------------------------------------------------
        if result.returncode != 0 or not os.path.exists(output_tmp_path) or os.path.getsize(
            output_tmp_path
        ) == 0:
            error_msg = result.stderr.strip() or f"Frame at index {frameNumber} could not be found."
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"error": error_msg},
            )

        # Read PNG bytes and return as image response
        with open(output_tmp_path, "rb") as img_file:
            png_bytes = img_file.read()
        return Response(content=png_bytes, media_type="image/png")

    finally:
        # ------------------------------------------------------------------
        # Cleanup temporary files safely
        # ------------------------------------------------------------------
        for path in (input_tmp_path, output_tmp_path):
            if path and os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)