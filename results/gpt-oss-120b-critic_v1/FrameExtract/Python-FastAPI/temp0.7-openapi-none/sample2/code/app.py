import asyncio
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile, status, Response
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI(
    title="FrameExtract",
    description="A simple webapp to extract a specific frame from an uploaded video",
    version="1.0.0",
)

# Security / resource limits
MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 MiB
MAX_FRAME_NUMBER = 10_000_000        # arbitrary upper bound to avoid excessive work
FFMPEG_TIMEOUT = 15                  # seconds


@app.post(
    "/extract",
    responses={
        200: {
            "description": "The extracted image if it was possible to extract the image",
            "content": {"image/png": {}},
        },
        404: {
            "description": "Frame not found at the specified index.",
            "content": {"application/json": {}},
        },
    },
)
async def extract_frame(
    videoFileName: str = Form(..., description="Original video file name"),
    frameNumber: int = Form(..., description="Zero‑based index of the frame to extract"),
    video: UploadFile = File(..., description="Video file"),
):
    """
    Extract a single frame from the uploaded video using ffmpeg.
    """
    # -------------------------------------------------------------------------
    # Input validation
    # -------------------------------------------------------------------------
    if frameNumber < 0 or frameNumber > MAX_FRAME_NUMBER:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": f"frameNumber must be between 0 and {MAX_FRAME_NUMBER}."},
        )

    # -------------------------------------------------------------------------
    # Create temporary files for the uploaded video and the resulting PNG
    # -------------------------------------------------------------------------
    input_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=Path(video.filename).suffix)
    output_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    input_path = input_tmp.name
    output_path = output_tmp.name
    input_tmp.close()
    output_tmp.close()

    try:
        # -----------------------------------------------------------------
        # Save uploaded video to disk while enforcing size limit
        # -----------------------------------------------------------------
        total_written = 0
        with open(input_path, "wb") as f:
            while True:
                chunk = await video.read(1024 * 1024)  # 1 MiB chunks
                if not chunk:
                    break
                total_written += len(chunk)
                if total_written > MAX_UPLOAD_SIZE:
                    return JSONResponse(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        content={"error": "Uploaded file exceeds the allowed size limit."},
                    )
                f.write(chunk)

        # -----------------------------------------------------------------
        # Build ffmpeg command
        # -----------------------------------------------------------------
        ffmpeg_cmd = [
            "ffmpeg",
            "-i",
            input_path,
            "-vf",
            f"select=eq(n\\,{frameNumber})",
            "-vframes",
            "1",
            "-f",
            "image2",
            "-y",  # overwrite output if it exists
            output_path,
        ]

        # -----------------------------------------------------------------
        # Run ffmpeg asynchronously with a timeout
        # -----------------------------------------------------------------
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=FFMPEG_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return JSONResponse(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                content={"error": "ffmpeg processing timed out."},
            )

        # -----------------------------------------------------------------
        # Verify that an image was produced
        # -----------------------------------------------------------------
        if (
            proc.returncode != 0
            or not os.path.exists(output_path)
            or os.path.getsize(output_path) == 0
        ):
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"error": f"Frame at index {frameNumber} could not be found."},
            )

        # -----------------------------------------------------------------
        # Return the PNG image
        # -----------------------------------------------------------------
        with open(output_path, "rb") as img_file:
            img_bytes = img_file.read()
        return Response(content=img_bytes, media_type="image/png")

    finally:
        # -----------------------------------------------------------------
        # Clean up temporary files
        # -----------------------------------------------------------------
        for path in (input_path, output_path):
            try:
                os.remove(path)
            except OSError:
                pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)