import asyncio
import os
import shutil
import subprocess
import tempfile
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
import uvicorn

MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 MB
MAX_FRAME_NUMBER = 36000
FFMPEG_TIMEOUT_SECONDS = 30
MAX_CONCURRENT_EXTRACTIONS = 2
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

_extraction_semaphore = asyncio.Semaphore(MAX_CONCURRENT_EXTRACTIONS)


def _ensure_ffmpeg_available() -> None:
    if shutil.which("ffmpeg") is None:
        raise HTTPException(
            status_code=500,
            detail="Server is not configured correctly.",
        )


def _validate_video_metadata(video_file_name: str, upload: UploadFile) -> str:
    _, ext = os.path.splitext(video_file_name or "")
    ext = ext.lower()

    if ext and ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported video file type.")

    if upload.content_type and upload.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported video content type.")

    return ext


async def _save_upload_with_limit(upload: UploadFile, destination_path: str) -> None:
    total_written = 0
    chunk_size = 1024 * 1024

    try:
        with open(destination_path, "wb") as output_file:
            while True:
                chunk = await upload.read(chunk_size)
                if not chunk:
                    break
                total_written += len(chunk)
                if total_written > MAX_UPLOAD_SIZE:
                    raise HTTPException(status_code=413, detail="Uploaded file is too large.")
                output_file.write(chunk)
    finally:
        await upload.close()


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
    frameNumber: Annotated[int, Form(ge=0, le=MAX_FRAME_NUMBER)],
    video: Annotated[UploadFile, File(...)],
):
    _ensure_ffmpeg_available()
    suffix = _validate_video_metadata(videoFileName, video)

    async with _extraction_semaphore:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, f"input{suffix}")
            output_path = os.path.join(tmpdir, "frame.png")

            await _save_upload_with_limit(video, input_path)

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
                result = await asyncio.to_thread(
                    subprocess.run,
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=FFMPEG_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                raise HTTPException(status_code=500, detail="Server is not configured correctly.")
            except FileNotFoundError:
                raise HTTPException(status_code=500, detail="Server is not configured correctly.")
            except OSError:
                raise HTTPException(status_code=500, detail="Server is not configured correctly.")

            if (
                result.returncode != 0
                or not os.path.exists(output_path)
                or os.path.getsize(output_path) == 0
            ):
                return JSONResponse(
                    status_code=404,
                    content={"error": f"Frame at second {frameNumber} could not be found."},
                )

            try:
                with open(output_path, "rb") as output_file:
                    image_bytes = output_file.read()
            except OSError:
                raise HTTPException(status_code=500, detail="Server is not configured correctly.")

    return Response(content=image_bytes, media_type="image/png")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)