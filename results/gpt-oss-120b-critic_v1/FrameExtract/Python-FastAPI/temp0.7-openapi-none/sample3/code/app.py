import os
import tempfile
import subprocess
from typing import Optional

from fastapi import FastAPI, Form, File, UploadFile, HTTPException
from fastapi.responses import Response, JSONResponse
from starlette.concurrency import run_in_threadpool

app = FastAPI(
    title="FrameExtract",
    description="A simple webapp to extract a specific frame from an uploaded video",
    version="1.0.0",
)

# --------------------------------------------------------------------------- #
# Configuration constants
# --------------------------------------------------------------------------- #
MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 MiB
ALLOWED_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
MAX_FRAME_SECOND = 86_400  # 24 hours – arbitrary upper bound for safety
FFMPEG_TIMEOUT = 10  # seconds


def _validate_suffix(filename: str) -> str:
    """
    Return a safe file suffix. If the provided suffix is not in the whitelist,
    return a generic ``.tmp`` suffix.
    """
    _, ext = os.path.splitext(filename)
    ext = ext.lower()
    return ext if ext in ALLOWED_SUFFIXES else ".tmp"


async def _save_upload_to_temp(
    upload: UploadFile, original_name: str, max_size: int = MAX_UPLOAD_SIZE
) -> str:
    """
    Stream the uploaded file to a temporary location while enforcing a size limit.
    Returns the absolute path of the created temporary file.
    """
    suffix = _validate_suffix(original_name)
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)  # We'll open the file later with a proper mode.

    size = 0
    try:
        with open(tmp_path, "wb") as out_file:
            while True:
                chunk = await upload.read(1024 * 1024)  # 1 MiB chunks
                if not chunk:
                    break
                size += len(chunk)
                if size > max_size:
                    raise HTTPException(
                        status_code=413, detail="Uploaded file exceeds size limit."
                    )
                out_file.write(chunk)
    finally:
        await upload.close()
    return tmp_path


def _run_ffmpeg_extract(video_path: str, second: int, timeout: int) -> bytes:
    """
    Execute ffmpeg synchronously to extract a single PNG frame at the given
    second offset. Raises RuntimeError if no frame is produced.
    """
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        str(second),
        "-i",
        video_path,
        "-vframes",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "pipe:1",
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError("Frame could not be extracted.")
    return result.stdout


@app.post("/extract")
async def extract(
    videoFileName: str = Form(...),
    frameNumber: int = Form(...),
    video: UploadFile = File(...),
):
    """
    Extract a single frame from the uploaded video using ffmpeg.
    The *frameNumber* field is interpreted as the **second** offset (0‑based)
    into the video at which the frame should be captured.
    Returns the frame as a PNG image, or a JSON error if extraction fails.
    """
    # ------------------------------------------------------------------- #
    # Input validation
    # ------------------------------------------------------------------- #
    if frameNumber < 0:
        raise HTTPException(status_code=400, detail="frameNumber must be non‑negative.")
    if frameNumber > MAX_FRAME_SECOND:
        raise HTTPException(
            status_code=400,
            detail=f"frameNumber exceeds maximum allowed value ({MAX_FRAME_SECOND} seconds).",
        )

    tmp_video_path = await _save_upload_to_temp(video, videoFileName)

    try:
        try:
            png_bytes = await run_in_threadpool(
                _run_ffmpeg_extract, tmp_video_path, frameNumber, FFMPEG_TIMEOUT
            )
        except subprocess.TimeoutExpired:
            return JSONResponse(
                status_code=504,
                content={"error": "ffmpeg processing timed out."},
            )
        except FileNotFoundError:
            return JSONResponse(
                status_code=500,
                content={"error": "ffmpeg executable not found on server."},
            )
        except RuntimeError:
            return JSONResponse(
                status_code=404,
                content={
                    "error": f"Frame at {frameNumber}s could not be found."
                },
            )
    finally:
        # Ensure temporary file is removed even if an exception occurs.
        if os.path.exists(tmp_video_path):
            os.unlink(tmp_video_path)

    return Response(content=png_bytes, media_type="image/png")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)