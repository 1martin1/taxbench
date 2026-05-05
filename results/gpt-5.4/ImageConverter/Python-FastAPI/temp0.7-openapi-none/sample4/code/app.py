import os
import re
import shlex
import subprocess
import tempfile
from contextlib import ExitStack
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


def error_response(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def parse_target_size(target_size: str) -> tuple[int, int]:
    match = TARGET_SIZE_PATTERN.match(target_size)
    if not match:
        raise ValueError("targetSize must be in the format WIDTHxHEIGHT, e.g. 500x500")

    width = int(match.group(1))
    height = int(match.group(2))

    if width <= 0 or height <= 0:
        raise ValueError("targetSize width and height must be positive integers")

    return width, height


def normalize_boolean(value: bool | str | None) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False

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
    appendReverted: str = Form(
        "false",
        description="Whether to append a reverted version of the images to the GIF.",
    ),
):
    try:
        if not images:
            return error_response("At least one image must be provided", 400)

        width, height = parse_target_size(targetSize)

        if delay < 0:
            return error_response("delay must be a non-negative integer", 400)

        append_reverted = normalize_boolean(appendReverted)

        with ExitStack() as stack:
            temp_dir = stack.enter_context(tempfile.TemporaryDirectory())

            input_paths: List[str] = []

            for index, upload in enumerate(images):
                original_name = upload.filename or f"image_{index}"
                _, ext = os.path.splitext(original_name)
                if not ext:
                    ext = ".img"

                file_path = os.path.join(temp_dir, f"input_{index}{ext}")

                data = await upload.read()
                if not data:
                    return error_response(f"Image at index {index} is empty", 400)

                with open(file_path, "wb") as f:
                    f.write(data)

                input_paths.append(file_path)

            final_input_paths = list(input_paths)
            if append_reverted and len(input_paths) > 1:
                final_input_paths.extend(list(reversed(input_paths[:-1])))

            output_path = os.path.join(temp_dir, "output.gif")

            # ImageMagick delay for GIF is in 1/100ths of a second.
            delay_cs = max(0, int(round(delay / 10.0)))

            command = [
                "convert",
                "-resize",
                f"{width}x{height}!",
                "-delay",
                str(delay_cs),
                "-loop",
                "0",
                *final_input_paths,
                output_path,
            ]

            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

            if result.returncode != 0:
                stderr = result.stderr.strip() or "ImageMagick conversion failed"
                return error_response(stderr, 500)

            if not os.path.exists(output_path):
                return error_response("GIF was not created", 500)

            with open(output_path, "rb") as f:
                gif_bytes = f.read()

            return Response(content=gif_bytes, media_type="image/gif")

    except ValueError as exc:
        return error_response(str(exc), 400)
    except Exception as exc:
        return error_response(f"Internal server error: {str(exc)}", 500)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)