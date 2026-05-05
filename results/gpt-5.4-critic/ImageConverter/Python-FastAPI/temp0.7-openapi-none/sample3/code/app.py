import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
from typing import List, Optional

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
import uvicorn


if importlib.util.find_spec("multipart") is None:
    raise RuntimeError(
        "python-multipart is required for multipart/form-data handling but is not installed."
    )

if shutil.which("convert") is None:
    raise RuntimeError(
        "ImageMagick 'convert' executable is required but was not found on PATH."
    )


app = FastAPI(
    title="GIF Creator API",
    description="API to create a GIF from a set of images with specified parameters.",
    version="1.0.0",
)


TARGET_SIZE_PATTERN = re.compile(r"^\s*(\d+)x(\d+)\s*$", re.IGNORECASE)

MAX_IMAGES = 50
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_DIMENSION = 2000
MAX_DELAY_MS = 10000
MAX_OUTPUT_GIF_BYTES = 50 * 1024 * 1024
CONVERT_TIMEOUT_SECONDS = 30

ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/tiff",
    "image/x-ms-bmp",
}

ALLOWED_MAGIC_NUMBERS = (
    (b"\xFF\xD8\xFF", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),
    (b"BM", "image/bmp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)


def error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def parse_target_size(target_size: str) -> tuple[int, int]:
    match = TARGET_SIZE_PATTERN.match(target_size)
    if not match:
        raise ValueError("targetSize must be in the format WIDTHxHEIGHT, e.g. 500x500")

    width = int(match.group(1))
    height = int(match.group(2))

    if width <= 0 or height <= 0:
        raise ValueError("targetSize width and height must be positive integers")

    if width > MAX_DIMENSION or height > MAX_DIMENSION:
        raise ValueError(
            f"targetSize width and height must not exceed {MAX_DIMENSION} pixels"
        )

    return width, height


def parse_bool_strict(value: object) -> bool:
    if isinstance(value, bool):
        return value

    if value is None:
        return False

    normalized = str(value).strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False

    raise ValueError("appendReverted must be a boolean value: true or false")


def detect_image_type(header: bytes) -> Optional[str]:
    for magic, image_type in ALLOWED_MAGIC_NUMBERS:
        if header.startswith(magic):
            if image_type == "image/webp":
                if len(header) >= 12 and header[8:12] == b"WEBP":
                    return image_type
                continue
            return image_type
    return None


def save_upload_to_disk(upload: UploadFile, output_path: str) -> int:
    total_written = 0
    header = b""

    with open(output_path, "wb") as destination:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break

            if len(header) < 16:
                needed = 16 - len(header)
                header += chunk[:needed]

            total_written += len(chunk)
            if total_written > MAX_FILE_SIZE_BYTES:
                raise ValueError(
                    f"Image '{upload.filename}' exceeds the maximum allowed size of {MAX_FILE_SIZE_BYTES} bytes"
                )

            destination.write(chunk)

    if total_written == 0:
        raise ValueError(f"Image '{upload.filename}' is empty")

    detected_type = detect_image_type(header)
    if detected_type is None:
        raise ValueError(f"Image '{upload.filename}' is not a supported image file")

    if upload.content_type not in ALLOWED_CONTENT_TYPES:
        raise ValueError(f"Image '{upload.filename}' has an unsupported content type")

    return total_written


def build_convert_command(
    frame_paths: List[str],
    width: int,
    height: int,
    delay_ms: int,
    output_gif_path: str,
) -> List[str]:
    command = [
        "convert",
        "-dispose",
        "previous",
        "-delay",
        f"{delay_ms}x1000",
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

    command.extend(
        [
            "-loop",
            "0",
            output_gif_path,
        ]
    )

    return command


def run_convert(command: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=CONVERT_TIMEOUT_SECONDS,
    )


def read_file_bytes(path: str) -> bytes:
    with open(path, "rb") as file_obj:
        return file_obj.read()


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
    temp_dir: Optional[str] = None
    try:
        if not images:
            return error_response(400, "At least one image must be provided")

        if len(images) > MAX_IMAGES:
            return error_response(400, f"No more than {MAX_IMAGES} images may be uploaded")

        width, height = parse_target_size(targetSize)

        if delay < 0:
            return error_response(400, "delay must be a non-negative integer")
        if delay > MAX_DELAY_MS:
            return error_response(400, f"delay must not exceed {MAX_DELAY_MS} milliseconds")

        try:
            append_reverted = parse_bool_strict(appendReverted)
        except ValueError as exc:
            return error_response(400, str(exc))

        temp_dir = tempfile.mkdtemp(prefix="gif_creator_")
        input_paths: List[str] = []
        total_upload_bytes = 0

        for index, upload in enumerate(images):
            try:
                if not upload.filename:
                    return error_response(400, f"Image at index {index} is missing a filename")

                output_path = os.path.join(temp_dir, f"input_{index}")

                written = await run_in_threadpool(save_upload_to_disk, upload, output_path)
                total_upload_bytes += written

                if total_upload_bytes > MAX_TOTAL_UPLOAD_BYTES:
                    return error_response(
                        400,
                        f"Total uploaded file size exceeds the maximum allowed size of {MAX_TOTAL_UPLOAD_BYTES} bytes",
                    )

                input_paths.append(output_path)
            finally:
                await upload.close()

        if append_reverted and len(input_paths) > 1:
            frame_paths = input_paths + input_paths[-2::-1]
        else:
            frame_paths = input_paths

        output_gif_path = os.path.join(temp_dir, "output.gif")
        command = build_convert_command(frame_paths, width, height, delay, output_gif_path)

        try:
            result = await run_in_threadpool(run_convert, command)
        except subprocess.TimeoutExpired:
            return error_response(500, "GIF creation failed")
        except FileNotFoundError:
            return error_response(500, "GIF creation failed")

        if result.returncode != 0:
            return error_response(500, "GIF creation failed")

        if not os.path.exists(output_gif_path):
            return error_response(500, "GIF creation failed")

        output_size = os.path.getsize(output_gif_path)
        if output_size <= 0:
            return error_response(500, "GIF creation failed")
        if output_size > MAX_OUTPUT_GIF_BYTES:
            return error_response(500, "GIF creation failed")

        gif_bytes = await run_in_threadpool(read_file_bytes, output_gif_path)
        return Response(content=gif_bytes, media_type="image/gif")

    except ValueError as exc:
        return error_response(400, str(exc))
    except Exception:
        return error_response(500, "Internal server error")
    finally:
        if temp_dir and os.path.isdir(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)