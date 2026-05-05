import os
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

app = FastAPI(
    title="FrameExtract",
    version="1.0.0",
    description="A simple webapp to extract a specific frame from an uploaded video",
)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 MiB
FFMPEG_TIMEOUT_SECONDS = 10
MAX_FRAME_NUMBER = 10_000_000  # arbitrary safety limit


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #
class ErrorResponse(BaseModel):
    error: str


# --------------------------------------------------------------------------- #
# Helper utilities
# --------------------------------------------------------------------------- #
def _json_error(message: str) -> JSONResponse:
    """Return a JSONResponse that matches the OpenAPI error schema."""
    return JSONResponse(status_code=404, content={"error": message})


async def _save_upload_to_tempfile(upload: UploadFile, max_size: int) -> str:
    """
    Stream the uploaded file to a temporary location without loading it fully
    into memory. Abort if the file exceeds *max_size* bytes.
    """
    suffix = Path(upload.filename).suffix or ".tmp"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        total = 0
        while True:
            chunk = await upload.read(1024 * 1024)  # 1 MiB per iteration
            if not chunk:
                break
            total += len(chunk)
            if total > max_size:
                # Clean up partially written file
                tmp_path = tmp.name
                tmp.close()
                os.remove(tmp_path)
                raise _json_error("Uploaded file exceeds the allowed size.")
            tmp.write(chunk)
        return tmp.name


def _is_video_mime(mime_type: str) -> bool:
    """Very simple MIME‑type check for video files."""
    return mime_type.startswith("video/")


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #
@app.post(
    "/extract",
    responses={
        200: {"content": {"image/png": {}}, "description": "The extracted image if it was possible to extract the image"},
        404: {"model": ErrorResponse, "description": "Frame not found at the specified index."},
    },
)
async def extract(
    videoFileName: str = Form(..., description="Original video file name, e.g., PXL_1000.mp4"),
    frameNumber: int = Form(..., description="Zero‑based index of the frame to extract"),
    video: UploadFile = File(..., description="Binary video file"),
):
    """
    Extract a single frame from the uploaded video using ffmpeg.
    Returns the frame as a PNG image or a JSON error if the frame cannot be found.
    """
    # ------------------------------------------------------------------- #
    # Input validation
    # ------------------------------------------------------------------- #
    if frameNumber < 0 or frameNumber > MAX_FRAME_NUMBER:
        return _json_error(f"Frame at index {frameNumber} could not be found.")

    if not _is_video_mime(video.content_type or ""):
        return _json_error("Uploaded file is not a recognized video type.")

    # ------------------------------------------------------------------- #
    # Store uploaded video safely
    # ------------------------------------------------------------------- #
    try:
        tmp_path = await _save_upload_to_tempfile(video, MAX_UPLOAD_SIZE)
    finally:
        await video.close()

    # ------------------------------------------------------------------- #
    # Run ffmpeg to extract the requested frame
    # ------------------------------------------------------------------- #
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        tmp_path,
        "-vf",
        f"select=eq(n\\,{frameNumber})",
        "-vframes",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "pipe:1",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        os.remove(tmp_path)
        return _json_error(f"Frame at index {frameNumber} could not be found.")
    finally:
        # Ensure temporary file is removed irrespective of ffmpeg outcome
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    if result.returncode != 0 or not result.stdout:
        return _json_error(f"Frame at index {frameNumber} could not be found.")

    # ------------------------------------------------------------------- #
    # Successful extraction – return PNG image
    # ------------------------------------------------------------------- #
    return Response(content=result.stdout, media_type="image/png")


# --------------------------------------------------------------------------- #
# Application entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)