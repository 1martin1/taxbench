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

MAX_IMAGES = 50
MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024
MAX_TARGET_DIMENSION = 4096
MAX_TOTAL_FRAMES = 99
CONVERT_TIMEOUT_SECONDS = 30
MAX_OUTPUT_GIF_SIZE_BYTES = 50 * 1024 * 1024
ALLOWED_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".webp",
    ".tif",
    ".tiff",
}


def error_response(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def parse_target_size(target_size: str) -> tuple[int, int]:
    match = TARGET_SIZE_PATTERN.match(target_size)
    if not match:
        raise ValueError("targetSize must be in the format WIDTHxHEIGHT, for example 500x500.")

    width = int(match.group(1))
    height = int(match.group(2))

    if width <= 0 or height <= 0:
        raise ValueError("targetSize width and height must be greater than 0.")

    if width > MAX_TARGET_DIMENSION or height > MAX_TARGET_DIMENSION:
        raise ValueError(
            f"targetSize width and height must be less than or equal to {MAX_TARGET_DIMENSION}."
        )

    return width, height


def ensure_convert_available() -> None:
    if shutil.which("convert") is None:
        raise RuntimeError("ImageMagick 'convert' command is not available on the server.")


def validate_image_upload(upload: UploadFile) -> None:
    if not upload.filename:
        raise ValueError("Each uploaded image must have a filename.")

    _, ext = os.path.splitext(upload.filename)
    if ext.lower() not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError("Each uploaded file must be a supported image type.")

    content_type = (upload.content_type or "").lower()
    if content_type and not content_type.startswith("image/"):
        raise ValueError("Each uploaded file must have an image content type.")


async def save_upload_file(upload: UploadFile, destination_path: str) -> None:
    total_written = 0
    with open(destination_path, "wb") as out_file:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            total_written += len(chunk)
            if total_written > MAX_UPLOAD_SIZE_BYTES:
                raise ValueError(
                    f"Each uploaded image must be less than or equal to {MAX_UPLOAD_SIZE_BYTES} bytes."
                )
            out_file.write(chunk)


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
    summary="Create a GIF from images",
    description="Accepts images, a target size, delay, and a flag to append a reverted version to create a GIF.",
)
async def create_gif(
    images: List[UploadFile] = File(..., description="Array of images to be included in the GIF."),
    targetSize: str = Form(..., description="Target size for the GIF in pixels (width x height)."),
    delay: int = Form(10, description="Delay between frames in milliseconds."),
    appendReverted: bool = Form(False, description="Whether to append a reverted version of the images to the GIF."),
):
    try:
        if not images:
            return error_response("At least one image must be provided.", 400)

        if len(images) > MAX_IMAGES:
            return error_response(f"No more than {MAX_IMAGES} images may be uploaded.", 400)

        try:
            width, height = parse_target_size(targetSize)
        except ValueError as exc:
            return error_response(str(exc), 400)

        if delay <= 0:
            return error_response("delay must be greater than 0.", 400)

        frame_delay_ticks = max(1, delay // 10)

        try:
            ensure_convert_available()
        except RuntimeError:
            return error_response("Internal server error.", 500)

        for image in images:
            try:
                validate_image_upload(image)
            except ValueError as exc:
                return error_response(str(exc), 400)

        total_frames = len(images)
        if appendReverted and len(images) > 1:
            total_frames += len(images) - 1

        if total_frames > MAX_TOTAL_FRAMES:
            return error_response(
                f"Total frame count after applying appendReverted must be less than or equal to {MAX_TOTAL_FRAMES}.",
                400,
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            input_paths: List[str] = []

            for index, image in enumerate(images):
                original_name = os.path.basename(image.filename)
                _, ext = os.path.splitext(original_name)
                ext = ext.lower() if ext else ".img"
                input_path = os.path.join(temp_dir, f"input_{index:04d}{ext}")
                try:
                    await save_upload_file(image, input_path)
                except ValueError as exc:
                    return error_response(str(exc), 400)
                input_paths.append(input_path)

            ordered_paths = list(input_paths)
            if appendReverted and len(input_paths) > 1:
                ordered_paths.extend(list(reversed(input_paths[:-1])))

            output_path = os.path.join(temp_dir, "output.gif")

            command = [
                "convert",
                "-dispose",
                "previous",
            ]

            for path in ordered_paths:
                command.extend(
                    [
                        "(",
                        path,
                        "-auto-orient",
                        "-resize",
                        f"{width}x{height}",
                        "-background",
                        "white",
                        "-gravity",
                        "center",
                        "-extent",
                        f"{width}x{height}",
                        ")",
                    ]
                )

            command.extend(
                [
                    "-delay",
                    str(frame_delay_ticks),
                    "-loop",
                    "0",
                    output_path,
                ]
            )

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
            except OSError:
                return error_response("Internal server error.", 500)

            if result.returncode != 0:
                return error_response("Failed to create GIF from the provided images.", 400)

            if not os.path.exists(output_path):
                return error_response("Internal server error.", 500)

            try:
                output_size = os.path.getsize(output_path)
            except OSError:
                return error_response("Internal server error.", 500)

            if output_size > MAX_OUTPUT_GIF_SIZE_BYTES:
                return error_response("Generated GIF is too large.", 400)

            try:
                with open(output_path, "rb") as gif_file:
                    gif_bytes = gif_file.read()
            except OSError:
                return error_response("Internal server error.", 500)

            return Response(content=gif_bytes, media_type="image/gif")

    except Exception:
        return error_response("Internal server error.", 500)
    finally:
        for image in images:
            try:
                await image.close()
            except Exception:
                pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)