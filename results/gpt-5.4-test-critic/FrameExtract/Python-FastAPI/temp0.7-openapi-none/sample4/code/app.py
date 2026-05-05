import os
import tempfile
import subprocess

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response
import uvicorn

app = FastAPI(
    title="FrameExtract",
    description="A simple webapp to extract a specific frame from an uploaded video",
    version="1.0.0",
)

MAX_UPLOAD_SIZE_BYTES = 100 * 1024 * 1024  # 100 MB
READ_CHUNK_SIZE = 1024 * 1024  # 1 MB
FFMPEG_TIMEOUT_SECONDS = 30
ALLOWED_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".webm",
    ".m4v",
    ".mpeg",
    ".mpg",
    ".wmv",
    ".3gp",
    ".flv",
    ".ogv",
}


@app.post(
    "/extract",
    responses={
        200: {
            "description": "The extracted image if it was possible to extract the image",
            "content": {"image/png": {"schema": {"type": "string", "format": "binary"}}},
        },
        404: {
            "description": "Frame not found at the specified second.",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "error": {
                                "type": "string",
                                "example": "Frame at second 132 could not be found.",
                            }
                        },
                    }
                }
            },
        },
    },
    summary="Attempt to extract the frame at the specified second from the user-uploaded video",
)
async def extract_frame(
    videoFileName: str = Form(...),
    frameNumber: int = Form(...),
    video: UploadFile = File(...),
):
    if frameNumber < 0:
        return JSONResponse(
            status_code=404,
            content={"error": f"Frame at second {frameNumber} could not be found."},
        )

    provided_extension = os.path.splitext(videoFileName)[1].lower()
    upload_extension = os.path.splitext(video.filename or "")[1].lower()

    chosen_extension = None
    if upload_extension in ALLOWED_EXTENSIONS:
        chosen_extension = upload_extension
    elif provided_extension in ALLOWED_EXTENSIONS:
        chosen_extension = provided_extension
    else:
        chosen_extension = ".mp4"

    with tempfile.TemporaryDirectory() as temp_dir:
        input_path = os.path.join(temp_dir, f"input{chosen_extension}")
        output_path = os.path.join(temp_dir, "frame.png")

        total_written = 0
        try:
            with open(input_path, "wb") as f:
                while True:
                    chunk = await video.read(READ_CHUNK_SIZE)
                    if not chunk:
                        break
                    total_written += len(chunk)
                    if total_written > MAX_UPLOAD_SIZE_BYTES:
                        return JSONResponse(
                            status_code=404,
                            content={"error": f"Frame at second {frameNumber} could not be found."},
                        )
                    f.write(chunk)
        finally:
            await video.close()

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(frameNumber),
            "-i",
            input_path,
            "-frames:v",
            "1",
            "-f",
            "image2",
            output_path,
        ]

        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=FFMPEG_TIMEOUT_SECONDS,
            )
        except (subprocess.TimeoutExpired, OSError):
            return JSONResponse(
                status_code=404,
                content={"error": f"Frame at second {frameNumber} could not be found."},
            )

        if result.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            return JSONResponse(
                status_code=404,
                content={"error": f"Frame at second {frameNumber} could not be found."},
            )

        with open(output_path, "rb") as f:
            image_bytes = f.read()

        return Response(content=image_bytes, media_type="image/png")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)