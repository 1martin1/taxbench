import os
import subprocess
import tempfile
from typing import Annotated

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response
import uvicorn

MAX_UPLOAD_SIZE_BYTES = 100 * 1024 * 1024  # 100 MiB
FFMPEG_TIMEOUT_SECONDS = 30
CHUNK_SIZE = 1024 * 1024  # 1 MiB


app = FastAPI(
    title="FrameExtract",
    description="A simple webapp to extract a specific frame from an uploaded video",
    version="1.0.0",
)


@app.middleware("http")
async def limit_request_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_UPLOAD_SIZE_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"error": "Uploaded file is too large."},
                )
        except ValueError:
            pass
    return await call_next(request)


@app.post(
    "/extract",
    responses={
        200: {
            "description": "The extracted image if it was possible to extract the image",
            "content": {
                "image/png": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
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
    videoFileName: Annotated[str, Form(...)],
    frameNumber: Annotated[int, Form(...)],
    video: Annotated[UploadFile, File(...)],
):
    if frameNumber < 0:
        return JSONResponse(
            status_code=404,
            content={"error": f"Frame at second {frameNumber} could not be found."},
        )

    suffix = (
        os.path.splitext(videoFileName)[1]
        or os.path.splitext(video.filename or "")[1]
        or ".mp4"
    )
    suffix = suffix[:16] if suffix else ".mp4"

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = os.path.join(temp_dir, f"input{suffix}")
            output_path = os.path.join(temp_dir, "frame.png")

            total_written = 0
            with open(input_path, "wb") as input_file:
                while True:
                    chunk = await video.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    total_written += len(chunk)
                    if total_written > MAX_UPLOAD_SIZE_BYTES:
                        return JSONResponse(
                            status_code=413,
                            content={"error": "Uploaded file is too large."},
                        )
                    input_file.write(chunk)

            if total_written == 0:
                return JSONResponse(
                    status_code=404,
                    content={"error": f"Frame at second {frameNumber} could not be found."},
                )

            command = [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                str(frameNumber),
                "-i",
                input_path,
                "-frames:v",
                "1",
                "-f",
                "image2",
                "-y",
                output_path,
            ]

            try:
                result = subprocess.run(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=FFMPEG_TIMEOUT_SECONDS,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return JSONResponse(
                    status_code=404,
                    content={"error": f"Frame at second {frameNumber} could not be found."},
                )

            if (
                result.returncode != 0
                or not os.path.exists(output_path)
                or os.path.getsize(output_path) == 0
            ):
                return JSONResponse(
                    status_code=404,
                    content={"error": f"Frame at second {frameNumber} could not be found."},
                )

            with open(output_path, "rb") as output_file:
                image_bytes = output_file.read()

            return Response(content=image_bytes, media_type="image/png")
    finally:
        await video.close()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)