import os
import subprocess
import tempfile

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

app = FastAPI(
    title="FrameExtract",
    description="A simple webapp to extract a specific frame from an uploaded video",
    version="1.0.0",
)


@app.post("/extract")
async def extract(
    videoFileName: str = Form(...),
    frameNumber: int = Form(...),
    video: UploadFile = File(...),
):
    """
    Extract a single frame from the uploaded video at the given frame index.
    Returns the frame as a PNG image. If the frame cannot be extracted,
    returns a 404 with a JSON error message.
    """
    # Save uploaded video to a temporary file
    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(videoFileName)[1]) as tmp_video:
        tmp_path = tmp_video.name
        content = await video.read()
        tmp_video.write(content)

    try:
        # Build ffmpeg command to output a single PNG frame to stdout
        # The filter selects the exact frame number (0‑based)
        filter_expr = f"select=eq(n\\,{frameNumber})"
        cmd = [
            "ffmpeg",
            "-i",
            tmp_path,
            "-vf",
            filter_expr,
            "-vframes",
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
            # Frame could not be extracted
            error_msg = f"Frame at index {frameNumber} could not be found."
            raise HTTPException(
                status_code=404,
                detail={"error": error_msg},
            )

        # Return the PNG image
        return Response(content=result.stdout, media_type="image/png")

    finally:
        # Clean up temporary video file
        try:
            os.remove(tmp_path)
        except OSError:
            pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)