import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

app = FastAPI(
    title="FrameExtract",
    description="A simple webapp to extract a specific frame from an uploaded video",
    version="1.0.0",
)

# --------------------------------------------------------------------------- #
# Configuration / limits
# --------------------------------------------------------------------------- #
MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 MiB
MAX_FRAME_SECONDS = 2 * 60 * 60      # 2 hours
FFMPEG_TIMEOUT = 15                 # seconds


def _ffmpeg_available() -> bool:
    """Return True if ffmpeg executable can be found."""
    return shutil.which("ffmpeg") is not None


@app.post("/extract")
async def extract(
    videoFileName: str = Form(...),
    frameNumber: int = Form(...),
    video: UploadFile = File(...),
):
    """
    Extract a single frame from the uploaded video using ffmpeg.

    The `frameNumber` is interpreted as the timestamp **in seconds** from the
    start of the video. The endpoint returns:

    * **200** – PNG image bytes of the extracted frame.
    * **404** – JSON error when the frame cannot be extracted.
    * **400** – Bad request (invalid parameters or oversized upload).
    * **500** – Server error (ffmpeg not available or internal failure).
    """
    # ------------------------------------------------------------------- #
    # Basic validation
    # ------------------------------------------------------------------- #
    if not _ffmpeg_available():
        raise HTTPException(status_code=500, detail="ffmpeg executable not found.")

    if frameNumber < 0 or frameNumber > MAX_FRAME_SECONDS:
        raise HTTPException(
            status_code=400,
            detail=f"frameNumber must be between 0 and {MAX_FRAME_SECONDS} seconds.",
        )

    # ------------------------------------------------------------------- #
    # Store uploaded video in a temporary file without loading whole content
    # into memory.
    # ------------------------------------------------------------------- #
    suffix = Path(video.filename).suffix or ".mp4"
    input_path = None
    output_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_in:
            input_path = tmp_in.name
            total_written = 0
            # Read and write in chunks, enforcing size limit.
            while True:
                chunk = await video.read(1024 * 1024)  # 1 MiB per iteration
                if not chunk:
                    break
                total_written += len(chunk)
                if total_written > MAX_UPLOAD_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail="Uploaded file exceeds the allowed size limit.",
                    )
                tmp_in.write(chunk)

        # ------------------------------------------------------------------- #
        # Prepare output temporary file for the PNG image.
        # ------------------------------------------------------------------- #
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp_out:
            output_path = tmp_out.name

        # ------------------------------------------------------------------- #
        # Build ffmpeg command:
        #   -ss <seconds>  : seek to the requested timestamp
        #   -i <input>     : input file
        #   -frames:v 1    : capture exactly one video frame
        #   -f image2      : force image format
        #   -y             : overwrite output if it exists
        # ------------------------------------------------------------------- #
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(frameNumber),
            "-i",
            input_path,
            "-frames:v",
            "1",
            "-f",
            "image2",
            output_path,
        ]

        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=FFMPEG_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(
                status_code=500,
                detail="ffmpeg processing timed out.",
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=500,
                detail="ffmpeg executable not found.",
            )

        # ------------------------------------------------------------------- #
        # Evaluate ffmpeg result.
        # ------------------------------------------------------------------- #
        if result.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(
            output_path
        ) == 0:
            error_msg = f"Frame at {frameNumber} seconds could not be found."
            return JSONResponse(content={"error": error_msg}, status_code=404)

        # ------------------------------------------------------------------- #
        # Return PNG bytes.
        # ------------------------------------------------------------------- #
        with open(output_path, "rb") as f:
            png_bytes = f.read()
        return Response(content=png_bytes, media_type="image/png")

    finally:
        # ------------------------------------------------------------------- #
        # Cleanup temporary files regardless of success or failure.
        # ------------------------------------------------------------------- #
        for path in (input_path, output_path):
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except Exception:
                    pass  # Silently ignore cleanup errors.

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)