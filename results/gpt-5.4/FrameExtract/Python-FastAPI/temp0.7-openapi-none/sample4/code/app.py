import os
import tempfile
import subprocess
from typing import Optional

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
            "content": {"image/png": {}},
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
    summary="Attempt to extract the frame at the specified index from the user-uploaded video",
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

    original_extension = os.path.splitext(videoFileName)[1]
    if not original_extension:
        original_extension = ".bin"

    with tempfile.TemporaryDirectory() as temp_dir:
        input_path = os.path.join(temp_dir, f"input{original_extension}")
        output_path = os.path.join(temp_dir, "frame.png")

        file_bytes = await video.read()
        with open(input_path, "wb") as f:
            f.write(file_bytes)

        select_filter = f"select=eq(n\\,{frameNumber})"

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            input_path,
            "-vf",
            select_filter,
            "-frames:v",
            "1",
            "-vsync",
            "0",
            output_path,
        ]

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
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