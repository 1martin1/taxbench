import os
import re
import subprocess
import tempfile
import threading
from typing import List, Optional

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
import uvicorn


app = FastAPI(
    title="GIF Creator API",
    description="API to create a GIF from a set of images with specified parameters.",
    version="1.0.0",
)


TARGET_SIZE_PATTERN = re.compile(r"^\s*(\d+)x(\d+)\s*$", re.IGNORECASE)

MAX_IMAGES = 100
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB per file
MAX_TOTAL_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB total per request
MAX_WIDTH = 2000
MAX_HEIGHT = 2000
MAX_DELAY = 10000
CONVERT_TIMEOUT_SECONDS = 60
MAX_OUTPUT_GIF_BYTES = 50 * 1024 * 1024
MAX_CONCURRENT_CONVERSIONS = 2

IMAGE_SIGNATURES = {
    "jpeg": [b"\xff\xd8\xff"],
    "png": [b"\x89PNG\r\n\x1a\n"],
    "gif": [b"GIF87a", b"GIF89a"],
    "bmp": [b"BM"],
    "webp": [b"RIFF"],
    "tiff": [b"II*\x00", b"MM\x00*"],
}

conversion_semaphore = threading.BoundedSemaphore(MAX_CONCURRENT_CONVERSIONS)


def error_response(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    missing_fields = []

    for err in errors:
        if err.get("type") == "missing":
            loc = err.get("loc", [])
            if len(loc) >= 2 and loc[0] == "body":
                missing_fields.append(str(loc[-1]))

    if missing_fields:
        unique_fields = []
        seen = set()
        for field in missing_fields:
            if field not in seen:
                seen.add(field)
                unique_fields.append(field)
        return error_response(f"Missing required field(s): {', '.join(unique_fields)}.", 400)

    for err in errors:
        loc = err.get("loc", [])
        if len(loc) >= 2 and loc[0] == "body":
            field = str(loc[-1])
            if field == "delay":
                return error_response("delay must be an integer.", 400)
            if field == "appendReverted":
                return error_response("appendReverted must be a boolean.", 400)
            if field == "targetSize":
                return error_response("targetSize must be provided as a string in the format WIDTHxHEIGHT.", 400)
            if field == "images":
                return error_response("images must be uploaded as files.", 400)

    return error_response("Invalid request.", 400)


def parse_target_size(target_size: str) -> tuple[int, int]:
    match = TARGET_SIZE_PATTERN.match(target_size)
    if not match:
        raise ValueError("targetSize must be in the format WIDTHxHEIGHT, for example 500x500.")

    width = int(match.group(1))
    height = int(match.group(2))

    if width <= 0 or height <= 0:
        raise ValueError("targetSize width and height must be positive integers.")

    if width > MAX_WIDTH or height > MAX_HEIGHT:
        raise ValueError(f"targetSize exceeds the maximum allowed dimensions of {MAX_WIDTH}x{MAX_HEIGHT}.")

    return width, height


def is_supported_image_bytes(header: bytes) -> bool:
    if header.startswith(b"RIFF") and len(header) >= 12 and header[8:12] == b"WEBP":
        return True

    for signatures in IMAGE_SIGNATURES.values():
        for signature in signatures:
            if header.startswith(signature):
                return True
    return False


async def save_upload_file(upload: UploadFile, destination_path: str, remaining_total_bytes: int) -> int:
    total_written = 0
    header = b""

    with open(destination_path, "wb") as out_file:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break

            if len(header) < 64:
                needed = 64 - len(header)
                header += chunk[:needed]

            total_written += len(chunk)
            if total_written > MAX_FILE_SIZE_BYTES:
                raise ValueError(f"Uploaded file '{upload.filename or 'unknown'}' exceeds the maximum allowed size.")

            if total_written > remaining_total_bytes:
                raise ValueError("Total uploaded file size exceeds the maximum allowed limit.")

            out_file.write(chunk)

    await upload.close()

    if total_written == 0:
        raise ValueError(f"Uploaded file '{upload.filename or 'unknown'}' is empty.")

    if not is_supported_image_bytes(header):
        raise ValueError(f"Uploaded file '{upload.filename or 'unknown'}' is not a supported image.")

    return total_written


def acquire_conversion_slot() -> bool:
    return conversion_semaphore.acquire(blocking=False)


def release_conversion_slot() -> None:
    try:
        conversion_semaphore.release()
    except ValueError:
        pass


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
                        "properties": {"error": {"type": "string"}},
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
                        "properties": {"error": {"type": "string"}},
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
    slot_acquired = False
    try:
        if not images:
            return error_response("At least one image must be provided.", 400)

        if len(images) > MAX_IMAGES:
            return error_response(f"Too many images provided. Maximum allowed is {MAX_IMAGES}.", 400)

        try:
            width, height = parse_target_size(targetSize)
        except ValueError as exc:
            return error_response(str(exc), 400)

        if delay < 0:
            return error_response("delay must be a non-negative integer.", 400)

        if delay > MAX_DELAY:
            return error_response(f"delay exceeds the maximum allowed value of {MAX_DELAY}.", 400)

        if not acquire_conversion_slot():
            return error_response("Server is busy processing requests. Please try again later.", 500)
        slot_acquired = True

        with tempfile.TemporaryDirectory() as temp_dir:
            input_paths: List[str] = []
            total_uploaded_bytes = 0

            for index, upload in enumerate(images):
                if not upload.filename:
                    return error_response("Each uploaded image must have a filename.", 400)

                safe_name = os.path.basename(upload.filename)
                input_path = os.path.join(temp_dir, f"input_{index}_{safe_name}")

                try:
                    written = await save_upload_file(
                        upload,
                        input_path,
                        MAX_TOTAL_UPLOAD_BYTES - total_uploaded_bytes,
                    )
                except ValueError as exc:
                    return error_response(str(exc), 400)

                total_uploaded_bytes += written
                input_paths.append(input_path)

            if not input_paths:
                return error_response("No valid images were uploaded.", 400)

            ordered_paths = list(input_paths)
            if appendReverted and len(input_paths) > 1:
                ordered_paths.extend(list(reversed(input_paths[:-1])))

            output_path = os.path.join(temp_dir, "output.gif")

            command = [
                "convert",
                "-limit",
                "memory",
                "128MiB",
                "-limit",
                "map",
                "256MiB",
                "-delay",
                str(delay),
                "-loop",
                "0",
            ]

            for path in ordered_paths:
                command.extend([path, "-resize", f"{width}x{height}!"])

            command.append(output_path)

            try:
                result = subprocess.run(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=CONVERT_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                return error_response("Internal server error.", 500)
            except Exception:
                return error_response("Internal server error.", 500)

            if result.returncode != 0:
                return error_response("Invalid image input or GIF creation failed.", 400)

            if not os.path.exists(output_path):
                return error_response("Internal server error.", 500)

            output_size = os.path.getsize(output_path)
            if output_size <= 0:
                return error_response("Internal server error.", 500)

            if output_size > MAX_OUTPUT_GIF_BYTES:
                return error_response("Generated GIF exceeds the maximum allowed size.", 400)

            with open(output_path, "rb") as gif_file:
                gif_bytes = gif_file.read(MAX_OUTPUT_GIF_BYTES + 1)

            if len(gif_bytes) > MAX_OUTPUT_GIF_BYTES:
                return error_response("Generated GIF exceeds the maximum allowed size.", 400)

            return Response(content=gif_bytes, media_type="image/gif")

    except Exception:
        return error_response("Internal server error.", 500)
    finally:
        if slot_acquired:
            release_conversion_slot()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)