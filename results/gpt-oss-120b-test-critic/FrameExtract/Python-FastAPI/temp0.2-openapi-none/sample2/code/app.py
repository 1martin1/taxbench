import os
import shutil
import subprocess
import tempfile
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import JSONResponse, Response
from fastapi.exception_handlers import http_exception_handler
import uvicorn

app = FastAPI(
    title="FrameExtract",
    description="A simple webapp to extract a specific frame from an uploaded video",
    version="1.0.0",
)

# ---------- Configuration ----------
MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 MB
MAX_FRAME_SECONDS = 24 * 60 * 60  # 24 hours, arbitrary safety limit
FFMPEG_TIMEOUT = 15  # seconds
# -----------------------------------


def _ensure_ffmpeg_available() -> None:
    """Check that ffmpeg binary is available in PATH."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg binary not found in PATH. Please install ffmpeg.")


@app.on_event("startup")
def startup_event():
    _ensure_ffmpeg_available()


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    """Return JSON error payload for HTTPExceptions."""
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.post("/extract")
async def extract(
    videoFileName: str = Form(...),
    frameNumber: int = Form(...),
    video: UploadFile = File(...),
):
    """
    Extract a single frame from the uploaded video at the given *second* (frameNumber).
    Returns the frame as a PNG image.
    """
    # ---------- Validate inputs ----------
    if frameNumber < 0:
        raise HTTPException(status_code=400, detail="frameNumber must be non‑negative.")
    if frameNumber > MAX_FRAME_SECONDS:
        raise HTTPException(
            status_code=400,
            detail=f"frameNumber exceeds maximum allowed value of {MAX_FRAME_SECONDS} seconds.",
        )
    # -------------------------------------

    # ---------- Save uploaded video safely ----------
    suffix = os.path.splitext(video.filename)[1] or ".mp4"
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_video:
            total_written = 0
            while True:
                chunk = await video.read(1024 * 1024)  # 1 MB chunks
                if not chunk:
                    break
                total_written += len(chunk)
                if total_written > MAX_UPLOAD_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Uploaded file exceeds size limit of {MAX_UPLOAD_SIZE // (1024 * 1024)} MB.",
                    )
                tmp_video.write(chunk)
            tmp_video_path = tmp_video.name
    finally:
        await video.close()
    # -----------------------------------------------

    try:
        # Build ffmpeg command:
        # -ss seeks to the requested timestamp (seconds)
        # -frames:v 1 extracts a single frame
        # Output as PNG to stdout
        ffmpeg_cmd = [
            "ffmpeg",
            "-loglevel",
            "error",
            "-ss",
            str(frameNumber),
            "-i",
            tmp_video_path,
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "pipe:1",
        ]

        try:
            result = subprocess.run(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=FFMPEG_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(
                status_code=504,
                detail="ffmpeg processing timed out.",
            )
        except FileNotFoundError:
            # This should not happen because we checked at startup,
            # but handle gracefully.
            raise HTTPException(
                status_code=500,
                detail="ffmpeg binary not found on server.",
            )

        if result.returncode != 0 or not result.stdout:
            # Frame could not be extracted (e.g., timestamp beyond video length)
            raise HTTPException(
                status_code=404,
                detail=f"Frame at second {frameNumber} could not be found.",
            )

        return Response(content=result.stdout, media_type="image/png")
    finally:
        # Clean up temporary file
        try:
            os.remove(tmp_video_path)
        except OSError:
            pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)