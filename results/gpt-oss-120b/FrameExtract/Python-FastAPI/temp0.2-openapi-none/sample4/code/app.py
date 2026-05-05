import os
import subprocess
import tempfile
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Response
from fastapi.responses import JSONResponse
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
    video: UploadFile = File(..., description="Video file binary"),
):
    # Save uploaded video to a temporary file
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(videoFileName)[1]) as tmp_video:
            tmp_path = tmp_video.name
            content = await video.read()
            tmp_video.write(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write temporary video file: {e}")

    # Prepare ffmpeg command to extract the requested frame as PNG to stdout
    # Using the select filter to pick the exact frame number (zero‑based)
    ffmpeg_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-i", tmp_path,
        "-vf", f"select=eq(n\\,{frameNumber})",
        "-vframes", "1",
        "-f", "image2pipe",
        "-vcodec", "png",
        "pipe:1",
    ]

    try:
        result = subprocess.run(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    finally:
        # Clean up the temporary video file
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if result.returncode != 0 or not result.stdout:
        # Frame could not be extracted
        error_msg = f"Frame at index {frameNumber} could not be found."
        return JSONResponse(status_code=404, content={"error": error_msg})

    # Return the PNG image bytes
    return Response(content=result.stdout, media_type="image/png")


if __name__ == "__main__":
    # Run the app on 0.0.0.0:5000
    uvicorn.run(app, host="0.0.0.0", port=5000)