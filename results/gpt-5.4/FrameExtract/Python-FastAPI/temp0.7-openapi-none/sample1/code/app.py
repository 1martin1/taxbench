import os
import tempfile
import subprocess

from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import Response, JSONResponse
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
    summary="Attempt to extract the frame at the specified index from the user-uploaded video",
)
async def extract_frame(
    videoFileName: str = Form(..., example="PXL_1000.mp4"),
    frameNumber: int = Form(..., example=132),
    video: UploadFile = File(...),
):
    if frameNumber < 0:
        return JSONResponse(
            status_code=404,
            content={"error": f"Frame at index {frameNumber} could not be found."},
        )

    video_suffix = os.path.splitext(videoFileName)[1] or ".bin"

    with tempfile.TemporaryDirectory() as temp_dir:
        input_path = os.path.join(temp_dir, f"input{video_suffix}")
        output_path = os.path.join(temp_dir, "frame.png")

        file_bytes = await video.read()
        with open(input_path, "wb") as f:
            f.write(file_bytes)

        cmd = [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            input_path,
            "-vf",
            f"select=eq(n\\,{frameNumber})",
            "-vframes",
            "1",
            "-fps_mode",
            "vfr",
            output_path,
            "-y",
        ]

        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except FileNotFoundError:
            raise HTTPException(status_code=500, detail="ffmpeg is not installed or not available.")

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