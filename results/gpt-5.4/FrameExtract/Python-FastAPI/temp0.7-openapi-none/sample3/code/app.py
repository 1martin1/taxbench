import os
import shutil
import subprocess
import tempfile

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response
import uvicorn

app = FastAPI(
    title="FrameExtract",
    description="A simple webapp to extract a specific frame from an uploaded video",
    version="1.0.0",
)


@app.post(
    "/extract",
    responses={
        200: {
            "description": "The extracted image if it was possible to extract the image",
            "content": {"image/png": {"schema": {"type": "string", "format": "binary"}}},
        },
        404: {
            "description": "Frame not found at the specified index.",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "error": {
                                "type": "string",
                                "example": "Frame at index 132 could not be found.",
                            }
                        },
                    }
                }
            },
        },
    },
)
async def extract_frame(
    videoFileName: str = Form(...),
    frameNumber: int = Form(...),
    video: UploadFile = File(...),
):
    if frameNumber < 0:
        return JSONResponse(
            status_code=404,
            content={"error": f"Frame at index {frameNumber} could not be found."},
        )

    suffix = os.path.splitext(videoFileName)[1] or os.path.splitext(video.filename or "")[1]

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = os.path.join(tmpdir, f"input{suffix}")
        output_path = os.path.join(tmpdir, "frame.png")

        with open(input_path, "wb") as f:
            shutil.copyfileobj(video.file, f)

        command = [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            input_path,
            "-vf",
            f"select=eq(n\\,{frameNumber})",
            "-vframes",
            "1",
            "-vsync",
            "0",
            "-f",
            "image2",
            output_path,
            "-y",
        ]

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        if result.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            return JSONResponse(
                status_code=404,
                content={"error": f"Frame at index {frameNumber} could not be found."},
            )

        with open(output_path, "rb") as f:
            image_bytes = f.read()

    return Response(content=image_bytes, media_type="image/png")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)