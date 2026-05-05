import os
import shutil
import subprocess
import tempfile
from typing import Any

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response
import uvicorn


MAX_UPLOAD_SIZE_BYTES = 100 * 1024 * 1024
READ_CHUNK_SIZE = 1024 * 1024
FFMPEG_TIMEOUT_SECONDS = 30

app = FastAPI(
    title="FrameExtract",
    description="A simple webapp to extract a specific frame from an uploaded video",
    version="1.0.0",
    redirect_slashes=False,
)


def _error_response(frame_number: int) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": f"Frame at index {frame_number} could not be found."},
    )


def _get_safe_suffix(upload: UploadFile, video_file_name: str) -> str:
    candidates = []

    if upload.filename:
        candidates.append(os.path.splitext(upload.filename)[1])
    if video_file_name:
        candidates.append(os.path.splitext(video_file_name)[1])

    for suffix in candidates:
        if suffix and len(suffix) <= 10 and suffix.startswith(".") and suffix.replace(".", "").isalnum():
            return suffix.lower()

    return ".bin"


async def _save_upload_with_limit(upload: UploadFile, destination_path: str) -> bool:
    total_written = 0
    try:
        with open(destination_path, "wb") as out_file:
            while True:
                chunk = await upload.read(READ_CHUNK_SIZE)
                if not chunk:
                    break
                total_written += len(chunk)
                if total_written > MAX_UPLOAD_SIZE_BYTES:
                    return False
                out_file.write(chunk)
    finally:
        await upload.close()
    return True


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
    videoFileName: str = Form(..., json_schema_extra={"example": "PXL_1000.mp4"}),
    frameNumber: int = Form(..., json_schema_extra={"example": 132}),
    video: UploadFile = File(...),
) -> Response:
    if frameNumber < 0:
        return _error_response(frameNumber)

    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path is None:
        return _error_response(frameNumber)

    video_suffix = _get_safe_suffix(video, videoFileName)

    with tempfile.TemporaryDirectory() as temp_dir:
        input_path = os.path.join(temp_dir, f"input{video_suffix}")
        output_path = os.path.join(temp_dir, "frame.png")

        saved_ok = await _save_upload_with_limit(video, input_path)
        if not saved_ok:
            return _error_response(frameNumber)

        cmd = [
            ffmpeg_path,
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
            "-y",
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
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return _error_response(frameNumber)

        if result.returncode != 0:
            return _error_response(frameNumber)

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            return _error_response(frameNumber)

        with open(output_path, "rb") as image_file:
            image_bytes = image_file.read()

        return Response(content=image_bytes, media_type="image/png")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)