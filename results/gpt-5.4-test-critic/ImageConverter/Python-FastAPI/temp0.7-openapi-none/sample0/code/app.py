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
MAX_DELAY_MS = 60000
MAX_DIMENSION = 10000


def error_response(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def parse_target_size(value: str) -> tuple[int, int]:
    match = TARGET_SIZE_PATTERN.match(value)
    if not match:
        raise ValueError("targetSize must be in the format WIDTHxHEIGHT, e.g. 500x500")

    width = int(match.group(1))
    height = int(match.group(2))

    if width <= 0 or height <= 0:
        raise ValueError("targetSize width and height must be greater than 0")

    if width > MAX_DIMENSION or height > MAX_DIMENSION:
        raise ValueError(f"targetSize dimensions must not exceed {MAX_DIMENSION}x{MAX_DIMENSION}")

    return width, height


def parse_bool_form(value: bool | str) -> bool:
    if isinstance(value, bool):
        return value

    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off", ""}:
        return False

    raise ValueError("appendReverted must be a boolean value")


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
    summary="Create a GIF from images",
    description="Accepts images, a target size, delay, and a flag to append a reverted version to create a GIF.",
)
async def create_gif(
    images: List[UploadFile] = File(..., description="Array of images to be included in the GIF."),
    targetSize: str = Form(..., description="Target size for the GIF in pixels (width x height)."),
    delay: int = Form(10, description="Delay between frames in milliseconds."),
    appendReverted: str = Form(
        "false",
        description="Whether to append a reverted version of the images to the GIF.",
    ),
):
    try:
        if not images:
            return error_response("At least one image must be provided", 400)

        if len(images) > MAX_FILES:
            return error_response(f"Too many images provided; maximum is {MAX_FILES}", 400)

        try:
            width, height = parse_target_size(targetSize)
        except ValueError as exc:
            return error_response(str(exc), 400)

        if delay < 0 or delay > MAX_DELAY_MS:
            return error_response(f"delay must be between 0 and {MAX_DELAY_MS} milliseconds", 400)

        try:
            append_reverted = parse_bool_form(appendReverted)
        except ValueError as exc:
            return error_response(str(exc), 400)

        with tempfile.TemporaryDirectory(prefix="gif_creator_") as temp_dir:
            input_paths: list[str] = []

            for index, upload in enumerate(images):
                if not upload.filename:
                    filename = f"image_{index}"
                else:
                    filename = os.path.basename(upload.filename)

                safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
                if not safe_name:
                    safe_name = f"image_{index}"

                destination_path = os.path.join(temp_dir, f"{index:04d}_{safe_name}")

                try:
                    with open(destination_path, "wb") as output_file:
                        shutil.copyfileobj(upload.file, output_file)
                finally:
                    await upload.close()

                if os.path.getsize(destination_path) == 0:
                    return error_response(f"Uploaded image at index {index} is empty", 400)

                input_paths.append(destination_path)

            if append_reverted and len(input_paths) > 1:
                frame_paths = input_paths + input_paths[-2::-1]
            else:
                frame_paths = input_paths

            output_path = os.path.join(temp_dir, "output.gif")

            command = [
                "convert",
                "-delay",
                str(delay // 10 if delay >= 0 else 1),
                "-loop",
                "0",
            ]

            for frame_path in frame_paths:
                command.extend(
                    [
                        frame_path,
                        "-resize",
                        f"{width}x{height}!",
                    ]
                )

            command.append(output_path)

            try:
                result = subprocess.run(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=300,
                )
            except subprocess.TimeoutExpired:
                return error_response("GIF creation timed out", 500)
            except Exception:
                return error_response("Failed to execute ImageMagick convert command", 500)

            if result.returncode != 0:
                stderr_text = result.stderr.decode("utf-8", errors="replace").strip()
                if not stderr_text:
                    stderr_text = "ImageMagick failed to create the GIF"
                return error_response(stderr_text, 400)

            if not os.path.exists(output_path):
                return error_response("GIF creation failed: output file was not generated", 500)

            with open(output_path, "rb") as gif_file:
                gif_bytes = gif_file.read()

            return Response(content=gif_bytes, media_type="image/gif")

    except Exception:
        return error_response("Internal Server Error", 500)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)