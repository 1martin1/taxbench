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
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB per file


def error_response(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def parse_target_size(target_size: str) -> tuple[int, int]:
    match = TARGET_SIZE_PATTERN.match(target_size)
    if not match:
        raise ValueError("targetSize must be in the format WIDTHxHEIGHT, for example 500x500.")

    width = int(match.group(1))
    height = int(match.group(2))

    if width <= 0 or height <= 0:
        raise ValueError("targetSize width and height must be positive integers.")

    return width, height


def parse_append_reverted(value: bool | str) -> bool:
    if isinstance(value, bool):
        return value

    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off", ""}:
        return False

    raise ValueError("appendReverted must be a boolean value.")


def sanitize_filename(filename: str | None, fallback: str) -> str:
    if not filename:
        return fallback

    name = os.path.basename(filename)
    if not name:
        return fallback

    sanitized = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return sanitized or fallback


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
            return error_response("At least one image must be provided.", 400)

        if len(images) > MAX_FILES:
            return error_response(f"Too many images provided. Maximum allowed is {MAX_FILES}.", 400)

        try:
            width, height = parse_target_size(targetSize)
        except ValueError as exc:
            return error_response(str(exc), 400)

        if delay < 0:
            return error_response("delay must be a non-negative integer.", 400)

        try:
            append_reverted = parse_append_reverted(appendReverted)
        except ValueError as exc:
            return error_response(str(exc), 400)

        if shutil.which("convert") is None:
            return error_response("ImageMagick 'convert' command is not available on the server.", 500)

        with tempfile.TemporaryDirectory() as tmpdir:
            input_paths: List[str] = []

            for index, upload in enumerate(images):
                safe_name = sanitize_filename(upload.filename, f"image_{index}")
                input_path = os.path.join(tmpdir, f"{index:04d}_{safe_name}")

                data = await upload.read()
                await upload.close()

                if not data:
                    return error_response(f"Image at index {index} is empty.", 400)

                if len(data) > MAX_FILE_SIZE_BYTES:
                    return error_response(
                        f"Image '{upload.filename or index}' exceeds the maximum allowed size of {MAX_FILE_SIZE_BYTES} bytes.",
                        400,
                    )

                with open(input_path, "wb") as f:
                    f.write(data)

                input_paths.append(input_path)

            frame_paths = list(input_paths)
            if append_reverted and len(input_paths) > 1:
                frame_paths.extend(list(reversed(input_paths)))

            output_path = os.path.join(tmpdir, "output.gif")

            command = [
                "convert",
                "-dispose",
                "background",
                "-delay",
                str(delay),
            ]

            for frame_path in frame_paths:
                command.extend(
                    [
                        frame_path,
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

            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

            if result.returncode != 0:
                stderr = (result.stderr or "").strip()
                if stderr:
                    return error_response(f"Failed to create GIF: {stderr}", 400)
                return error_response("Failed to create GIF due to an unknown conversion error.", 400)

            if not os.path.exists(output_path):
                return error_response("GIF creation failed: output file was not generated.", 500)

            with open(output_path, "rb") as f:
                gif_bytes = f.read()

            return Response(content=gif_bytes, media_type="image/gif")

    except Exception as exc:
        return error_response(f"Internal server error: {str(exc)}", 500)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)