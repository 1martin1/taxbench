import os
import subprocess
import tempfile
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, JSONResponse
import uvicorn

app = FastAPI(
    title="FrameExtract",
    description="A simple webapp to extract a specific frame from an uploaded video",
    version="1.0.0",
)


@app.post("/extract")
async def extract_frame(
    videoFileName: str = Form(..., description="Original video file name"),
    frameNumber: int = Form(..., description="Zero‑based index of the frame to extract"),
    video: UploadFile = File(..., description="Video file (binary)"),
):
    # Basic validation
    if frameNumber < 0:
        return JSONResponse(
            status_code=404,
            content={"error": f"Frame at index {frameNumber} could not be found."},
        )

    # Save uploaded video to a temporary file
    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(videoFileName)[1]) as tmp_video:
        tmp_video_path = tmp_video.name
        content = await video.read()
        tmp_video.write(content)

    try:
        # Build ffmpeg command to extract a single frame as PNG to stdout
        # Using select filter to pick the exact frame number (zero‑based)
        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-i", tmp_video_path,
            "-vf", f"select=eq(n\\,{frameNumber})",
            "-vframes", "1",
            "-f", "image2pipe",
            "-vcodec", "png",
            "-",
        ]

        result = subprocess.run(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        if result.returncode != 0 or not result.stdout:
            # Frame could not be extracted
            return JSONResponse(
                status_code=404,
                content={"error": f"Frame at index {frameNumber} could not be found."},
            )

        # Return PNG image
        return Response(content=result.stdout, media_type="image/png")

    finally:
        # Clean up temporary video file
        try:
            os.unlink(tmp_video_path)
        except OSError:
            pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)