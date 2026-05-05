import asyncio
import os
import re
import shutil
import subprocess
import tempfile
from typing import Optional

from fastapi import FastAPI, File, Form, Response, UploadFile
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI(
    title="FrameExtract",
    description="A simple webapp to extract a specific frame from an uploaded video",
    version="1.0.0",
)

MAX_UPLOAD_SIZE_BYTES = 100 * 1024 * 1024
FFPROBE_TIMEOUT_SECONDS = 10
FFMPEG_TIMEOUT_SECONDS = 20
MAX_CONCURRENT_JOBS = 2
PROCESSING_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
ALLOWED_CONTENT_TYPES = {
    "video/mp4",
    "video/mpeg",
    "video/quicktime",
    "video/x-msvideo",
    "video/x-matroska",
    "video/webm",
    "video/ogg",
    "application/octet-stream",
}
ALLOWED_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".webm",
    ".mpeg",
    ".mpg",
    ".ogv",
    ".m4v",
}


def sanitize_filename(filename: Optional[str]) -> str:
    if not filename:
        return "uploaded_video"
    name = os.path.basename(filename)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name or "uploaded_video"


def error_response(frame_number: int) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": f"Frame at second {frame_number} could not be found."},
    )


def tools_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def is_allowed_upload(video_file_name: str, upload: UploadFile) -> bool:
    ext = os.path.splitext(sanitize_filename(video_file_name))[1].lower()
    if ext and ext in ALLOWED_EXTENSIONS:
        return True
    if upload.content_type and upload.content_type.lower() in ALLOWED_CONTENT_TYPES:
        return True
    return False


async def save_upload_with_limit(upload: UploadFile, destination_path: str, max_bytes: int) -> bool:
    total = 0
    chunk_size = 1024 * 1024
    with open(destination_path, "wb") as out_file:
        while True:
            chunk = await upload.read(chunk_size)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                out_file.close()
                try:
                    os.remove(destination_path)
                except FileNotFoundError:
                    pass
                return False
            out_file.write(chunk)
    return True


def get_video_duration_seconds(video_path: str) -> Optional[float]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "format=duration",
        "-of",
        "default=nokey=1:noprint_wrappers=1",
        video_path,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=FFPROBE_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None

    output = result.stdout.strip()
    if not output or output == "N/A":
        return None

    try:
        duration = float(output)
    except ValueError:
        return None

    if duration < 0:
        return None
    return duration


def extract_frame_png(video_path: str, second: int) -> Optional[bytes]:
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-ss",
        str(second),
        "-i",
        video_path,
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "pipe:1",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            check=False,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0 or not result.stdout:
        return None

    return result.stdout


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
    openapi_extra={
        "requestBody": {
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "videoFileName": {"type": "string", "example": "PXL_1000.mp4"},
                            "frameNumber": {"type": "int", "example": 132},
                            "video": {"type": "string", "format": "binary"},
                        },
                    }
                }
            }
        }
    },
)
async def extract(
    videoFileName: str = Form(...),
    frameNumber: int = Form(...),
    video: UploadFile = File(...),
):
    if not tools_available():
        return error_response(frameNumber)

    if frameNumber < 0:
        return error_response(frameNumber)

    if not is_allowed_upload(videoFileName, video):
        return error_response(frameNumber)

    safe_name = sanitize_filename(videoFileName)
    suffix = os.path.splitext(safe_name)[1]
    if not suffix:
        suffix = ".bin"

    async with PROCESSING_SEMAPHORE:
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = os.path.join(tmpdir, f"input{suffix}")

            saved = await save_upload_with_limit(video, video_path, MAX_UPLOAD_SIZE_BYTES)
            await video.close()

            if not saved:
                return error_response(frameNumber)

            duration = await asyncio.to_thread(get_video_duration_seconds, video_path)
            if duration is not None and frameNumber >= int(duration if duration.is_integer() else duration + 0):
                return error_response(frameNumber)

            png_bytes = await asyncio.to_thread(extract_frame_png, video_path, frameNumber)
            if png_bytes is None:
                return error_response(frameNumber)

            return Response(content=png_bytes, media_type="image/png")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)