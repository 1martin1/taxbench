import imghdr
import math
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
import uvicorn


APP_TITLE = "GIF Creator API"
APP_DESCRIPTION = "API to create a GIF from a set of images with specified parameters."
APP_VERSION = "1.0.0"

TARGET_SIZE_PATTERN = re.compile(r"^\s*(\d+)x(\d+)\s*$")

MAX_FILES = 100
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024
MAX_WIDTH = 2000
MAX_HEIGHT = 2000
MAX_TOTAL_FRAMES = 199
SUBPROCESS_TIMEOUT_SECONDS = 30
ALLOWED_IMAGE_TYPES = {"jpeg", "png", "gif", "bmp", "tiff", "webp"}


def error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def parse_target_size(target_size: str) -> tuple[int, int]:
    match = TARGET_SIZE_PATTERN.fullmatch(target_size or "")
    if not match:
        raise ValueError("targetSize must be in the format WIDTHxHEIGHT, e.g. 500x500")

    width = int(match.group(1))
    height = int(match.group(2))

    if width <= 0 or height <= 0:
        raise ValueError("targetSize dimensions must be positive integers")

    if width > MAX_WIDTH or height > MAX_HEIGHT:
        raise ValueError(f"targetSize dimensions must not exceed {MAX_WIDTH}x{MAX_HEIGHT}")

    return width, height


def validate_delay(delay: int) -> None:
    if delay < 0:
        raise ValueError("delay must be greater than or equal to 0")


def validate_frame_count(image_count: int, append_reverted: bool) -> int:
    if image_count <= 0:
        raise ValueError("At least one image must be provided")

    total_frames = image_count
    if append_reverted:
        total_frames += image_count if image_count == 1 else image_count - 1

    if total_frames > MAX_TOTAL_FRAMES:
        raise ValueError(f"Total frame count must not exceed {MAX_TOTAL_FRAMES}")

    return total_frames


def delay_ms_to_centiseconds(delay_ms: int) -> int:
    return math.ceil(delay_ms / 10)


def detect_image_type(file_path: str) -> Optional[str]:
    with open(file_path, "rb") as file_obj:
        header = file_obj.read(512)
    return imghdr.what(None, h=header)


def ensure_dependencies() -> None:
    try:
        import multipart  # noqa: F401
    except Exception as exc:
        raise RuntimeError("python-multipart is required for multipart form handling") from exc

    convert_path = shutil.which("convert")
    if not convert_path:
        raise RuntimeError("ImageMagick 'convert' executable is not available in PATH")


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_dependencies()
    yield


app = FastAPI(
    title=APP_TITLE,
    description=APP_DESCRIPTION,
    version=APP_VERSION,
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    if errors:
        first_error = errors[0]
        location = [str(item) for item in first_error.get("loc", []) if item != "body"]
        field_name = location[-1] if location else "request"
        message = first_error.get("msg", "Invalid request")
        return error_response(400, f"Invalid value for {field_name}: {message}")
    return error_response(400, "Invalid request")


@app.post(
    "/create-gif",
    responses={
        200: {
            "description": "GIF created successfully",
            "content": {"image/gif": {"schema": {"type": "string", "format": "binary"}}},
        },
        400: {
            "description": "Bad Request",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "error": {
                                "type": "string",
                                "description": "Error message detailing why the request was invalid.",
                            }
                        },
                    }
                }
            },
        },
        500: {
            "description": "Internal Server Error",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "error": {
                                "type": "string",
                                "description": "Error message detailing what went wrong on the server.",
                            }
                        },
                    }
                }
            },
        },
    },
)
async def create_gif(
    images: List[UploadFile] = File(..., description="Array of images to be included in the GIF."),
    targetSize: str = Form(..., description="Target size for the GIF in pixels (width x height)."),
    delay: int = Form(10, description="Delay between frames in milliseconds."),
    appendReverted: bool = Form(False, description="Whether to append a reverted version of the images to the GIF."),
):
    if not images:
        return error_response(400, "At least one image must be provided")

    if len(images) > MAX_FILES:
        return error_response(400, f"Number of images must not exceed {MAX_FILES}")

    try:
        width, height = parse_target_size(targetSize)
        validate_delay(delay)
        validate_frame_count(len(images), appendReverted)
    except ValueError as exc:
        return error_response(400, str(exc))

    with tempfile.TemporaryDirectory() as temp_dir:
        input_paths: List[str] = []
        total_upload_size = 0

        try:
            for index, upload in enumerate(images):
                if not upload.filename:
                    return error_response(400, f"Image at index {index} is missing a filename")

                destination_path = os.path.join(temp_dir, f"input_{index}")
                bytes_written = 0

                with open(destination_path, "wb") as destination:
                    while True:
                        chunk = await upload.read(1024 * 1024)
                        if not chunk:
                            break
                        bytes_written += len(chunk)
                        total_upload_size += len(chunk)

                        if bytes_written > MAX_FILE_SIZE_BYTES:
                            return error_response(
                                400,
                                f"Image at index {index} exceeds the maximum allowed size of {MAX_FILE_SIZE_BYTES} bytes",
                            )

                        if total_upload_size > MAX_TOTAL_UPLOAD_SIZE_BYTES:
                            return error_response(
                                400,
                                f"Total upload size must not exceed {MAX_TOTAL_UPLOAD_SIZE_BYTES} bytes",
                            )

                        destination.write(chunk)

                if bytes_written == 0:
                    return error_response(400, f"Image at index {index} is empty")

                detected_type = detect_image_type(destination_path)
                if detected_type not in ALLOWED_IMAGE_TYPES:
                    return error_response(400, f"Image at index {index} is not a supported image file")

                input_paths.append(destination_path)

            frame_paths = list(input_paths)
            if appendReverted and len(input_paths) > 1:
                frame_paths.extend(reversed(input_paths[:-1]))
            elif appendReverted and len(input_paths) == 1:
                frame_paths.extend(input_paths)

            output_path = os.path.join(temp_dir, "output.gif")
            command = [
                "convert",
                "-delay",
                str(delay_ms_to_centiseconds(delay)),
                "-loop",
                "0",
            ]

            for path in frame_paths:
                command.extend(
                    [
                        path,
                        "-resize",
                        f"{width}x{height}",
                        "-background",
                        "white",
                        "-gravity",
                        "center",
                        "-extent",
                        f"{width}x{height}",
                    ]
                )

            command.append(output_path)

            try:
                result = subprocess.run(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=SUBPROCESS_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                return error_response(500, "Failed to create GIF")

            if result.returncode != 0:
                return error_response(500, "Failed to create GIF")

            if not os.path.exists(output_path):
                return error_response(500, "Failed to create GIF")

            with open(output_path, "rb") as gif_file:
                gif_bytes = gif_file.read()

            return Response(content=gif_bytes, media_type="image/gif")

        except Exception:
            return error_response(500, "Internal server error")
        finally:
            for upload in images:
                try:
                    await upload.close()
                except Exception:
                    pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)