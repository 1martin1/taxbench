import os
import shutil
import subprocess
import tempfile
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse, Response
import uvicorn

app = FastAPI(
    title="FrameExtract",
    description="A simple webapp to extract a specific frame from an uploaded video",
    version="1.0.0",
)

# Maximum allowed upload size (100 MB)
MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # bytes


def _ffmpeg_available() -> bool:
    """Check if ffmpeg executable is available in PATH."""
    return shutil.which("ffmpeg") is not None


@app.post("/extract")
async def extract_frame(
    videoFileName: str = Form(..., description="Original video file name"),
    frameNumber: int = Form(..., description="Index of the frame to extract (0‑based)"),
    video: UploadFile = File(..., description="Video file"),
):
    # Basic validation
    if frameNumber < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="frameNumber must be a non‑negative integer.",
        )

    if not _ffmpeg_available():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ffmpeg executable not found on server.",
        )

    # Enforce upload size limit
    content = await video.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Uploaded file exceeds size limit of {MAX_UPLOAD_SIZE // (1024 * 1024)} MB.",
        )

    # Save uploaded video to a temporary file
    suffix = os.path.splitext(video.filename)[1] or ".tmp"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_input:
        input_path = tmp_input.name
        tmp_input.write(content)

    # Prepare temporary output file for the extracted frame
    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp_output:
        output_path = tmp_output.name

    try:
        # Build ffmpeg command
        cmd = [
            "ffmpeg",
            "-loglevel",
            "error",
            "-i",
            input_path,
            "-vf",
            f"select=eq(n\\,{frameNumber})",
            "-vframes",
            "1",
            "-f",
            "image2",
            output_path,
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=False
            )
        except FileNotFoundError:
            # This should not happen because we checked earlier, but handle gracefully
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="ffmpeg executable not found on server.",
            )

        # If ffmpeg failed or the output file was not created, treat as not found
        if result.returncode != 0 or not os.path.exists(output_path):
            error_msg = f"Frame at index {frameNumber} could not be found."
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND, content={"error": error_msg}
            )

        # Read the PNG image bytes
        with open(output_path, "rb") as img_file:
            img_bytes = img_file.read()

        return Response(content=img_bytes, media_type="image/png")

    finally:
        # Clean up temporary files
        for path in (input_path, output_path):
            try:
                os.remove(path)
            except OSError:
                pass


if __name__ == "__main__":
    # Allow overriding the port via the PORT environment variable (useful for tests)
    port = int(os.getenv("PORT", "5000"))
    uvicorn.run(app, host="0.0.0.0", port=port)