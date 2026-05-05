import os
import shutil
import subprocess
import tempfile
from typing import Optional

from fastapi import FastAPI, File, Form, Request, Response, UploadFile
from fastapi.responses import JSONResponse
import uvicorn

MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 MiB
MAX_OUTPUT_SIZE = 20 * 1024 * 1024   # 20 MiB
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
    ".ogg",
    ".ogv",
}

ALLOWED_CONTENT_TYPES = {
    "video/mp4",
    "video/quicktime",
    "video/x-msvideo",
    "video/x-matroska",
    "video/webm",
    "video/x-m4v",
    "video/mpeg",
    "video/x-ms-wmv",
    "video/3gpp",
    "video/ogg",
    "application/octet-stream",
}

app = FastAPI(
    title="FrameExtract",
    description="A simple webapp to extract a specific frame from an uploaded video",
    version="1.0.0",
)


def _error_response(message: str, status_code: int = 404) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def _ensure_ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _get_extension(video_file_name: Optional[str], upload_filename: Optional[str]) -> str:
    source_name = video_file_name or upload_filename or ""
    _, ext = os.path.splitext(source_name)
    ext = ext.lower()
    if ext in ALLOWED_EXTENSIONS:
        return ext
    return ".bin"


def _is_allowed_upload(video_file_name: Optional[str], upload: UploadFile) -> bool:
    ext = _get_extension(video_file_name, upload.filename)
    content_type = (upload.content_type or "").lower()
    if ext in ALLOWED_EXTENSIONS:
        return True
    if content_type in ALLOWED_CONTENT_TYPES and content_type.startswith("video/"):
        return True
    return False


async def _save_upload_with_limit(upload: UploadFile, destination_path: str, max_size: int) -> bool:
    total_written = 0
    chunk_size = 1024 * 1024

    with open(destination_path, "wb") as out_file:
        while True:
            chunk = await upload.read(chunk_size)
            if not chunk:
                break
            total_written += len(chunk)
            if total_written > max_size:
                return False
            out_file.write(chunk)
    return True


def _extract_frame_with_ffmpeg(video_path: str, frame_number: int, output_path: str) -> bool:
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-nostdin",
        "-ss",
        str(frame_number),
        "-i",
        video_path,
        "-frames:v",
        "1",
        "-f",
        "image2",
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
    except (subprocess.TimeoutExpired, OSError):
        return False

    return (
        result.returncode == 0
        and os.path.exists(output_path)
        and os.path.getsize(output_path) > 0
        and os.path.getsize(output_path) <= MAX_OUTPUT_SIZE
    )


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
    request: Request,
    videoFileName: Optional[str] = Form(default=None),
    frameNumber: int = Form(...),
    video: UploadFile = File(...),
):
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_UPLOAD_SIZE:
                return _error_response(f"Frame at second {frameNumber} could not be found.", 404)
        except ValueError:
            return _error_response(f"Frame at second {frameNumber} could not be found.", 404)

    if frameNumber < 0:
        return _error_response(f"Frame at second {frameNumber} could not be found.", 404)

    if not _ensure_ffmpeg_available():
        return _error_response(f"Frame at second {frameNumber} could not be found.", 404)

    if not _is_allowed_upload(videoFileName, video):
        return _error_response(f"Frame at second {frameNumber} could not be found.", 404)

    suffix = _get_extension(videoFileName, video.filename)

    with tempfile.TemporaryDirectory() as temp_dir:
        input_path = os.path.join(temp_dir, f"input{suffix}")
        output_path = os.path.join(temp_dir, "frame.png")

        saved = await _save_upload_with_limit(video, input_path, MAX_UPLOAD_SIZE)
        await video.close()

        if not saved:
            return _error_response(f"Frame at second {frameNumber} could not be found.", 404)

        found = _extract_frame_with_ffmpeg(input_path, frameNumber, output_path)

        if not found:
            return _error_response(f"Frame at second {frameNumber} could not be found.", 404)

        try:
            with open(output_path, "rb") as f:
                image_bytes = f.read(MAX_OUTPUT_SIZE + 1)
        except OSError:
            return _error_response(f"Frame at second {frameNumber} could not be found.", 404)

        if not image_bytes or len(image_bytes) > MAX_OUTPUT_SIZE:
            return _error_response(f"Frame at second {frameNumber} could not be found.", 404)

        return Response(content=image_bytes, media_type="image/png", status_code=200)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)