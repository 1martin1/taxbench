import os
import subprocess
import tempfile

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse, Response
import uvicorn

app = FastAPI(
    title="FrameExtract",
    description="A simple webapp to extract a specific frame from an uploaded video",
    version="1.0.0",
)

# Maximum allowed upload size: 100 MB
MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 MB


@app.post(
    "/extract",
    responses={
        200: {
            "description": "The extracted image if it was possible to extract the image",
            "content": {"image/png": {"schema": {"type": "string", "format": "binary"}}},
        },
        400: {
            "description": "Bad request – invalid parameters or file",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"error": {"type": "string"}},
                    }
                }
            },
        },
        404: {
            "description": "Frame not found at the specified index.",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"error": {"type": "string"}},
                    }
                }
            },
        },
    },
)
async def extract_frame(
    videoFileName: str = Form(..., description="Original video file name"),
    frameNumber: int = Form(..., description="Zero‑based index of the frame to extract"),
    video: UploadFile = File(..., description="Video file (binary)"),
):
    """
    Extract a single frame from an uploaded video using ffmpeg.
    """
    # Validate frame number
    if frameNumber < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"frameNumber must be non‑negative, got {frameNumber}",
        )

    # Validate content type (basic check)
    if not video.content_type or not video.content_type.startswith("video/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file must be a video.",
        )

    # Save uploaded video to a temporary file while enforcing size limit
    suffix = os.path.splitext(videoFileName)[1] or ".tmp"
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_video:
            tmp_path = tmp_video.name
            total_read = 0
            while True:
                chunk = await video.read(1024 * 1024)  # 1 MiB chunks
                if not chunk:
                    break
                total_read += len(chunk)
                if total_read > MAX_UPLOAD_SIZE:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Uploaded file exceeds the maximum allowed size of 100 MiB.",
                    )
                tmp_video.write(chunk)
    except HTTPException:
        # Propagate HTTP errors
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to process uploaded file: {exc}",
        )

    # Build ffmpeg command to extract the requested frame as PNG
    ffmpeg_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
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
        "-",
    ]

    try:
        result = subprocess.run(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    finally:
        # Ensure temporary file is removed regardless of ffmpeg outcome
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if result.returncode != 0 or not result.stdout:
        # Frame could not be extracted
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": f"Frame at second {frameNumber} could not be found."},
        )

    # Successful extraction – return PNG image
    return Response(content=result.stdout, media_type="image/png")


if __name__ == "__main__":
    # Allow the port to be overridden via the PORT environment variable (useful for tests)
    port = int(os.getenv("PORT", "5000"))
    uvicorn.run(app, host="0.0.0.0", port=port)