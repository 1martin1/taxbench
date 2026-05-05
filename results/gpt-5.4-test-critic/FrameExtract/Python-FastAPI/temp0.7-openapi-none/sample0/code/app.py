import os
import shutil
import subprocess
import tempfile
from typing import Annotated

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response
import uvicorn

MAX_UPLOAD_SIZE_BYTES = 100 * 1024 * 1024
FFMPEG_TIMEOUT_SECONDS = 30
CHUNK_SIZE = 1024 * 1024

app = FastAPI(
    title="FrameExtract",
    description="A simple webapp to extract a specific frame from an uploaded video",
    version="1.0.0",
)


def _safe_suffix(video_file_name: str, upload_filename: str | None) -> str:
    suffix = os.path.splitext(video_file_name or "")[1]
    if not suffix:
        suffix = os.path.splitext(upload_filename or "")[1]
    if not suffix:
        suffix = ".mp4"
    if len(suffix) > 16:
        suffix = suffix[:16]
    if not suffix.startswith("."):
        suffix = f".{suffix}"
    return "".join(ch for ch in suffix if ch.isalnum() or ch == ".") or ".mp4"


async def _save_upload_with_limit(upload: UploadFile, destination_path: str) -> bool:
    total_written = 0
    with open(destination_path, "wb") as output_file:
        while True:
            chunk = await upload.read(CHUNK_SIZE)
            if not chunk:
                break
            total_written += len(chunk)
            if total_written > MAX_UPLOAD_SIZE_BYTES:
                return False
            output_file.write(chunk)
    return True


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
    videoFileName: Annotated[str, Form(...)],
    frameNumber: Annotated[int, Form(...)],
    video: Annotated[UploadFile, File(...)],
):
    if frameNumber < 0:
        return JSONResponse(
            status_code=404,
            content={"error": f"Frame at second {frameNumber} could not be found."},
        )

    suffix = _safe_suffix(videoFileName, video.filename)

    with tempfile.TemporaryDirectory() as temp_dir:
        input_path = os.path.join(temp_dir, f"input{suffix}")
        output_path = os.path.join(temp_dir, "frame.png")

        upload_ok = await _save_upload_with_limit(video, input_path)
        await video.close()

        if not upload_ok:
            return JSONResponse(
                status_code=404,
                content={"error": f"Frame at second {frameNumber} could not be found."},
            )

        command = [
            "ffmpeg",
            "-v",
            "error",
            "-nostdin",
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
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=FFMPEG_TIMEOUT_SECONDS,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return JSONResponse(
                status_code=404,
                content={"error": f"Frame at second {frameNumber} could not be found."},
            )

        if result.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            return JSONResponse(
                status_code=404,
                content={"error": f"Frame at second {frameNumber} could not be found."},
            )

        with open(output_path, "rb") as output_file:
            image_bytes = output_file.read()

        return Response(content=image_bytes, media_type="image/png")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)