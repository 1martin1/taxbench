import os
import subprocess
import tempfile
from typing import Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, Response
import uvicorn

MAX_UPLOAD_SIZE_BYTES = 100 * 1024 * 1024  # 100 MiB
FFMPEG_TIMEOUT_SECONDS = 30
READ_CHUNK_SIZE = 1024 * 1024  # 1 MiB

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
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid Content-Length header."},
            )
    return await call_next(request)


def custom_openapi() -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    extract_operation = openapi_schema.get("paths", {}).get("/extract", {}).get("post", {})
    request_body = (
        extract_operation.get("requestBody", {})
        .get("content", {})
        .get("multipart/form-data", {})
        .get("schema", {})
    )

    properties = request_body.get("properties", {})
    if "frameNumber" in properties:
        properties["frameNumber"] = {
            "type": "int",
            "example": 132,
        }

    responses = extract_operation.get("responses", {})
    if "404" in responses:
        responses["404"] = {
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
        }

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


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

    requested_suffix = os.path.splitext(videoFileName)[1]
    upload_suffix = os.path.splitext(video.filename or "")[1]
    suffix = requested_suffix or upload_suffix or ".bin"

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = os.path.join(tmpdir, f"input{suffix}")
        output_path = os.path.join(tmpdir, "frame.png")

        total_written = 0
        try:
            with open(input_path, "wb") as output_file:
                while True:
                    chunk = await video.read(READ_CHUNK_SIZE)
                    if not chunk:
                        break
                    total_written += len(chunk)
                    if total_written > MAX_UPLOAD_SIZE_BYTES:
                        return JSONResponse(
                            status_code=413,
                            content={"error": "Uploaded file is too large."},
                        )
                    output_file.write(chunk)
        finally:
            await video.close()

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
            output_path,
            "-y",
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

        with open(output_path, "rb") as image_file:
            image_bytes = image_file.read()

    return Response(content=image_bytes, media_type="image/png")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)