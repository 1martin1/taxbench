import os
import shutil
import subprocess
import tempfile
from typing import Annotated

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

MAX_UPLOAD_SIZE_BYTES = 100 * 1024 * 1024
MAX_OUTPUT_SIZE_BYTES = 20 * 1024 * 1024
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
    ".3gp",
    ".wmv",
    ".ogv",
}


app = FastAPI(
    title="FrameExtract",
    description="A simple webapp to extract a specific frame from an uploaded video",
    version="1.0.0",
)


def _safe_suffix(filename: str) -> str:
    suffix = os.path.splitext(filename or "")[1].lower()
    if suffix in ALLOWED_EXTENSIONS:
        return suffix
    return ".mp4"


def _copy_upload_with_limit(upload: UploadFile, destination_path: str, max_bytes: int) -> None:
    total_written = 0
    chunk_size = 1024 * 1024

    upload.file.seek(0)
    with open(destination_path, "wb") as destination:
        while True:
            chunk = upload.file.read(chunk_size)
            if not chunk:
                break
            total_written += len(chunk)
            if total_written > max_bytes:
                raise HTTPException(status_code=400, detail="Uploaded file is too large.")
            destination.write(chunk)


@app.post(
    "/extract",
    responses={
        200: {
            "description": "The extracted image if it was possible to extract the image",
            "content": {"image/png": {}},
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
        raise HTTPException(status_code=400, detail="frameNumber must be greater than or equal to 0.")

    original_name = videoFileName or (video.filename or "uploaded_video.mp4")
    suffix = _safe_suffix(original_name)

    content_type = (video.content_type or "").lower()
    if content_type and not (
        content_type.startswith("video/")
        or content_type in {"application/octet-stream"}
    ):
        raise HTTPException(status_code=400, detail="Uploaded file must be a video.")

    with tempfile.TemporaryDirectory() as temp_dir:
        input_path = os.path.join(temp_dir, f"input{suffix}")
        output_path = os.path.join(temp_dir, "frame.png")

        _copy_upload_with_limit(video, input_path, MAX_UPLOAD_SIZE_BYTES)

        cmd = [
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
        except (FileNotFoundError, subprocess.TimeoutExpired):
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

        output_size = os.path.getsize(output_path)
        if output_size > MAX_OUTPUT_SIZE_BYTES:
            return JSONResponse(
                status_code=404,
                content={"error": f"Frame at second {frameNumber} could not be found."},
            )

        with open(output_path, "rb") as f:
            image_bytes = f.read(MAX_OUTPUT_SIZE_BYTES + 1)

        if len(image_bytes) == 0 or len(image_bytes) > MAX_OUTPUT_SIZE_BYTES:
            return JSONResponse(
                status_code=404,
                content={"error": f"Frame at second {frameNumber} could not be found."},
            )

    return Response(content=image_bytes, media_type="image/png")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)