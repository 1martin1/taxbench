import os
import shutil
import subprocess
import tempfile
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import JSONResponse, Response

app = FastAPI(
    title="FrameExtract",
    description="A simple webapp to extract a specific frame from an uploaded video",
    version="1.0.0",
)

# -------------------------------------------------------------------------
# Configuration & utilities
# -------------------------------------------------------------------------
MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 MiB


def _ensure_ffmpeg_available() -> None:
    """Verify that the ffmpeg binary is present; raise a clear error otherwise."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError(
            "ffmpeg binary not found. Please ensure ffmpeg is installed and in PATH."
        )


def _read_upload_to_tempfile(
    upload: UploadFile, filename: str, max_size: int
) -> str:
    """
    Stream the uploaded file to a temporary location while enforcing a size limit.

    Returns the path to the temporary file.
    """
    suffix = os.path.splitext(filename)[1] or ""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    total_read = 0
    try:
        while True:
            chunk = upload.file.read(1024 * 1024)  # 1 MiB per iteration
            if not chunk:
                break
            total_read += len(chunk)
            if total_read > max_size:
                raise HTTPException(
                    status_code=413,
                    detail="Uploaded file exceeds the maximum allowed size of 100 MiB.",
                )
            tmp.write(chunk)
    finally:
        tmp.close()
    return tmp.name


def _extract_frame_at_timestamp(
    video_path: str, timestamp: float
) -> Optional[bytes]:
    """
    Use ffmpeg to seek to *timestamp* (seconds) and output a single PNG frame.

    Returns the PNG bytes on success, or None if ffmpeg fails to produce output.
    """
    cmd = [
        "ffmpeg",
        "-ss",
        str(timestamp),
        "-i",
        video_path,
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "-",
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout


# -------------------------------------------------------------------------
# Startup validation
# -------------------------------------------------------------------------
@app.on_event("startup")
def _check_dependencies():
    try:
        _ensure_ffmpeg_available()
    except RuntimeError as exc:
        # Fail fast – the application cannot serve requests without ffmpeg.
        raise RuntimeError(str(exc))


# -------------------------------------------------------------------------
# Endpoint
# -------------------------------------------------------------------------
@app.post("/extract")
async def extract(
    videoFileName: str = Form(...),
    frameNumber: int = Form(..., description="Time offset in seconds"),
    video: UploadFile = File(...),
):
    """
    Extract a single frame from the uploaded video at the given *frameNumber*,
    interpreted as a time offset in seconds. Returns the frame as a PNG image.
    If the frame cannot be extracted, returns a 404 with a JSON body:
    `{ "error": "..."}`
    """
    # -----------------------------------------------------------------
    # Save uploaded video safely
    # -----------------------------------------------------------------
    temp_video_path = _read_upload_to_tempfile(
        upload=video, filename=videoFileName, max_size=MAX_UPLOAD_SIZE
    )

    try:
        # -----------------------------------------------------------------
        # Extract frame using ffmpeg
        # -----------------------------------------------------------------
        png_bytes = _extract_frame_at_timestamp(
            video_path=temp_video_path, timestamp=float(frameNumber)
        )
        if not png_bytes:
            error_msg = f"Frame at timestamp {frameNumber} could not be found."
            return JSONResponse(
                status_code=404,
                content={"error": error_msg},
            )
        # -----------------------------------------------------------------
        # Successful response
        # -----------------------------------------------------------------
        return Response(content=png_bytes, media_type="image/png")
    finally:
        # Ensure temporary file is removed regardless of outcome
        try:
            os.remove(temp_video_path)
        except OSError:
            pass


# -------------------------------------------------------------------------
# Run the application
# -------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)