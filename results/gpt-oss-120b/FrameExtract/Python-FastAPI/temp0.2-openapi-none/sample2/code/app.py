import subprocess
import tempfile
import os
from fastapi import FastAPI, File, UploadFile, Form
from fastapi.responses import Response, JSONResponse

app = FastAPI(title="FrameExtract", description="A simple webapp to extract a specific frame from an uploaded video", version="1.0.0")


@app.post("/extract")
async def extract(
    videoFileName: str = Form(...),
    frameNumber: int = Form(...),
    video: UploadFile = File(...),
):
    # Save uploaded video to a temporary file
    suffix = os.path.splitext(video.filename)[1] or ".mp4"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_video:
        content = await video.read()
        tmp_video.write(content)
        tmp_video_path = tmp_video.name

    try:
        # Build ffmpeg command to extract the requested frame as PNG to stdout
        # Using zero‑based frame indexing (ffmpeg's select filter)
        ffmpeg_cmd = [
            "ffmpeg",
            "-loglevel", "error",          # suppress non‑error output
            "-i", tmp_video_path,
            "-vf", f"select=eq(n\\,{frameNumber})",
            "-vframes", "1",
            "-f", "image2pipe",
            "-vcodec", "png",
            "pipe:1",
        ]

        result = subprocess.run(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        if result.returncode != 0 or not result.stdout:
            # Frame could not be extracted
            return JSONResponse(
                status_code=404,
                content={"error": f"Frame at index {frameNumber} could not be found."},
            )

        # Successful extraction – return PNG image
        return Response(content=result.stdout, media_type="image/png")
    finally:
        # Clean up temporary video file
        try:
            os.remove(tmp_video_path)
        except OSError:
            pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)