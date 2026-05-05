import asyncio
import os
import tempfile
from typing import Optional

import uvicorn
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, Response


MAX_UPLOAD_SIZE_BYTES = 100 * 1024 * 1024
MAX_SECOND = 24 * 60 * 60
FFMPEG_TIMEOUT_SECONDS = 20
MAX_CONCURRENT_JOBS = 2
ALLOWED_CONTENT_TYPES = {
    "video/mp4",
    "video/quicktime",
    "video/x-msvideo",
    "video/x-matroska",
    "video/webm",
    "video/mpeg",
    "video/3gpp",
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
    ".3gp",
    ".ogv",
}


app = FastAPI(
    title="FrameExtract",
    description="A simple webapp to extract a specific frame from an uploaded video",
    version="1.0.0",
)

_ffmpeg_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)


def _is_valid_video_filename(name: str) -> bool:
    if not name or len(name) > 255:
        return False
    if name != os.path.basename(name):
        return False
    if any(ch in name for ch in ("\x00", "/", "\\")):
        return False
    _, ext = os.path.splitext(name)
    return ext.lower() in ALLOWED_EXTENSIONS


async def _save_upload_to_path(upload: UploadFile, destination_path: str) -> None:
    total_written = 0
    chunk_size = 1024 * 1024

    with open(destination_path, "wb") as destination:
        while True:
            chunk = await upload.read(chunk_size)
            if not chunk:
                break
            total_written += len(chunk)
            if total_written > MAX_UPLOAD_SIZE_BYTES:
                raise ValueError("Uploaded file is too large.")
            destination.write(chunk)


async def _extract_frame_png(video_path: str, second_number: int) -> Optional[bytes]:
    if second_number < 0 or second_number > MAX_SECOND:
        return None

    async with _ffmpeg_semaphore:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "frame.png")

            command = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                str(second_number),
                "-i",
                video_path,
                "-frames:v",
                "1",
                "-f",
                "image2",
                output_path,
            ]

            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    _, _ = await asyncio.wait_for(
                        process.communicate(),
                        timeout=FFMPEG_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    process.kill()
                    await process.communicate()
                    return None
            except OSError:
                return None

            if process.returncode != 0 or not os.path.exists(output_path):
                return None

            try:
                with open(output_path, "rb") as image_file:
                    return image_file.read()
            except OSError:
                return None


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
)
async def extract(
    videoFileName: str = Form(...),
    frameNumber: int = Form(...),
    video: UploadFile = File(...),
):
    try:
        if not _is_valid_video_filename(videoFileName):
            return JSONResponse(
                status_code=404,
                content={"error": f"Frame at second {frameNumber} could not be found."},
            )

        if frameNumber < 0 or frameNumber > MAX_SECOND:
            return JSONResponse(
                status_code=404,
                content={"error": f"Frame at second {frameNumber} could not be found."},
            )

        if video.content_type not in ALLOWED_CONTENT_TYPES:
            return JSONResponse(
                status_code=404,
                content={"error": f"Frame at second {frameNumber} could not be found."},
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, videoFileName)

            try:
                await _save_upload_to_path(video, input_path)
            except ValueError:
                return JSONResponse(
                    status_code=404,
                    content={"error": f"Frame at second {frameNumber} could not be found."},
                )

            image_bytes = await _extract_frame_png(input_path, frameNumber)

        if image_bytes is None:
            return JSONResponse(
                status_code=404,
                content={"error": f"Frame at second {frameNumber} could not be found."},
            )

        return Response(content=image_bytes, media_type="image/png")
    finally:
        await video.close()


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    path_item = schema.get("paths", {}).get("/extract", {}).get("post", {})
    request_body = path_item.get("requestBody", {})
    content = request_body.get("content", {}).get("multipart/form-data", {})
    body_schema = content.get("schema", {})
    properties = body_schema.get("properties", {})

    if "frameNumber" in properties:
        properties["frameNumber"]["type"] = "integer"
        properties["frameNumber"]["example"] = 132

    responses = path_item.get("responses", {})
    response_404 = responses.get("404", {})
    response_404["description"] = "Frame not found at the specified second."
    error_schema = (
        response_404.get("content", {})
        .get("application/json", {})
        .get("schema", {})
        .get("properties", {})
        .get("error", {})
    )
    if error_schema:
        error_schema["example"] = "Frame at second 132 could not be found."

    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)