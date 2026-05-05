import os
import subprocess
import tempfile
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import Response, JSONResponse
import uvicorn

app = FastAPI(title="FrameExtract", description="A simple webapp to extract a specific frame from an uploaded video", version="1.0.0")


@app.post("/extract")
async def extract_frame(
    videoFileName: str = Form(..., description="Original video file name"),
    frameNumber: int = Form(..., description="Index of the frame to extract (0‑based)"),
    video: UploadFile = File(..., description="Video file")
):
    # Save uploaded video to a temporary file
    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(video.filename)[1]) as tmp_input:
        input_path = tmp_input.name
        content = await video.read()
        tmp_input.write(content)

    # Prepare temporary output file for the extracted frame
    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp_output:
        output_path = tmp_output.name

    try:
        # Build ffmpeg command
        # -loglevel error : suppress non‑error output
        # -vf "select=eq(n\,FRAME)" : select the exact frame (0‑based)
        # -vframes 1 : output only one frame
        cmd = [
            "ffmpeg",
            "-loglevel", "error",
            "-i", input_path,
            "-vf", f"select=eq(n\\,{frameNumber})",
            "-vframes", "1",
            "-f", "image2",
            output_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        # If ffmpeg failed or the output file was not created, treat as not found
        if result.returncode != 0 or not os.path.exists(output_path):
            error_msg = f"Frame at index {frameNumber} could not be found."
            raise HTTPException(status_code=404, detail=error_msg)

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
    # Run the app on 0.0.0.0:5000
    uvicorn.run(app, host="0.0.0.0", port=5000)