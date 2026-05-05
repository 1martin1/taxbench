import os
import re
import shutil
import subprocess
import tempfile
from typing import List

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response
import uvicorn


app = FastAPI(
    title="GIF Creator API",
    description="API to create a GIF from a set of images with specified parameters.",
    version="1.0.0",
)


TARGET_SIZE_PATTERN = re.compile(r"^\s*(\d+)x(\d+)\s*$", re.IGNORECASE)
MAX_FILES = 500
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024


def error_response(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def parse_target_size(target_size: str) -> tuple[int, int]:
    match = TARGET_SIZE_PATTERN.match(target_size or "")
    if not match:
        raise ValueError("targetSize must be in the format WIDTHxHEIGHT, for example 500x500.")

    width = int(match.group(1))
    height = int(match.group(2))

    if width <= 0 or height <= 0:
        raise ValueError("targetSize width and height must be positive integers.")

    return width, height


def normalize_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False

    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off", ""}:
        return False

    raise ValueError("appendReverted must be a boolean value.")


def validate_delay(delay: int) -> int:
    if delay is None:
        return 10
    if delay < 0:
        raise ValueError("delay must be greater than or equal to 0.")
    return delay


def ensure_imagemagick_available() -> None:
    if shutil.which("convert") is None:
        raise RuntimeError("ImageMagick 'convert' command is not available on the server.")


async def save_upload_file(upload: UploadFile, destination_path: str) -> None:
    total_written = 0
    with open(destination_path, "wb") as output_file:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            total_written += len(chunk)
            if total_written > MAX_FILE_SIZE_BYTES:
                raise ValueError(f"Uploaded file '{upload.filename or 'unknown'}' exceeds the maximum allowed size.")
            output_file.write(chunk)
    await upload.close()


@app.post(
    "/create-gif",
    responses={
        200: {
            "description": "GIF created successfully",
            "content": {"image/gif": {}},
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
    summary="Create a GIF from images",
    description="Accepts images, a target size, delay, and a flag to append a reverted version to create a GIF.",
)
async def create_gif(
    images: List[UploadFile] = File(..., description="Array of images to be included in the GIF."),
    targetSize: str = Form(..., description="Target size for the GIF in pixels (width x height)."),
    delay: int = Form(10, description="Delay between frames in milliseconds."),
    appendReverted: str = Form("false", description="Whether to append a reverted version of the images to the GIF."),
):
    try:
        ensure_imagemagick_available()

        if not images:
            return error_response("At least one image must be provided.", 400)

        if len(images) > MAX_FILES:
            return error_response(f"Too many images provided. Maximum allowed is {MAX_FILES}.", 400)

        width, height = parse_target_size(targetSize)
        validated_delay = validate_delay(delay)
        append_reverted = normalize_bool(appendReverted)

        with tempfile.TemporaryDirectory() as temp_dir:
            input_paths: List[str] = []

            for index, upload in enumerate(images):
                if not upload.filename:
                    return error_response("Each uploaded image must have a filename.", 400)

                media_type = upload.content_type or ""
                if media_type and not media_type.startswith("image/"):
                    return error_response(f"Uploaded file '{upload.filename}' is not an image.", 400)

                safe_name = os.path.basename(upload.filename)
                input_path = os.path.join(temp_dir, f"{index:04d}_{safe_name}")
                try:
                    await save_upload_file(upload, input_path)
                except ValueError as exc:
                    return error_response(str(exc), 400)

                if not os.path.exists(input_path) or os.path.getsize(input_path) == 0:
                    return error_response(f"Uploaded file '{upload.filename}' is empty or invalid.", 400)

                input_paths.append(input_path)

            frame_paths = list(input_paths)
            if append_reverted and len(input_paths) > 1:
                frame_paths.extend(list(reversed(input_paths)))

            output_path = os.path.join(temp_dir, "output.gif")

            resize_arg = f"{width}x{height}!"
            command = [
                "convert",
                "-delay",
                str(validated_delay),
                "-loop",
                "0",
            ]

            for frame_path in frame_paths:
                command.extend(
                    [
                        "(",
                        frame_path,
                        "-resize",
                        resize_arg,
                        "-background",
                        "none",
                        "-gravity",
                        "center",
                        "-extent",
                        f"{width}x{height}",
                        ")"
                    ]
                )

            command.append(output_path)

            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

            if result.returncode != 0:
                error_message = result.stderr.strip() or "Failed to create GIF."
                return error_response(error_message, 400)

            if not os.path.exists(output_path):
                return error_response("GIF was not created.", 500)

            with open(output_path, "rb") as gif_file:
                gif_bytes = gif_file.read()

            return Response(content=gif_bytes, media_type="image/gif")

    except ValueError as exc:
        return error_response(str(exc), 400)
    except Exception as exc:
        return error_response(f"Internal server error: {str(exc)}", 500)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)