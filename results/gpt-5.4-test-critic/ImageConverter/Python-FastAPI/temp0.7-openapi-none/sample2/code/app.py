import os
import re
import shlex
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
        raise ValueError("targetSize dimensions must be positive integers.")

    return width, height


def parse_bool_form(value) -> bool:
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


def ensure_convert_exists() -> None:
    if shutil.which("convert") is None:
        raise RuntimeError("ImageMagick 'convert' command is not available on the server.")


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
                            "error": {"type": "string"},
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
                            "error": {"type": "string"},
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
    appendReverted: str = Form("false", description="Whether to append a reverted version of the images to the GIF."),
):
    try:
        ensure_convert_exists()

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
            append_reverted = parse_bool_form(appendReverted)
        except ValueError as exc:
            return error_response(str(exc), 400)

        with tempfile.TemporaryDirectory() as temp_dir:
            input_paths: List[str] = []

            for index, upload in enumerate(images):
                if not upload.filename:
                    return error_response("Each uploaded image must have a filename.", 400)

                original_name = os.path.basename(upload.filename)
                _, ext = os.path.splitext(original_name)
                if not ext:
                    ext = ".img"

                input_path = os.path.join(temp_dir, f"input_{index:04d}{ext}")
                file_bytes = await upload.read()

                if not file_bytes:
                    return error_response(f"Uploaded image '{original_name}' is empty.", 400)

                if len(file_bytes) > MAX_FILE_SIZE_BYTES:
                    return error_response(
                        f"Uploaded image '{original_name}' exceeds the maximum allowed size of {MAX_FILE_SIZE_BYTES} bytes.",
                        400,
                    )

                with open(input_path, "wb") as f:
                    f.write(file_bytes)

                input_paths.append(input_path)
                await upload.close()

            if append_reverted and len(input_paths) > 1:
                frame_paths = input_paths + list(reversed(input_paths[:-1]))
            else:
                frame_paths = list(input_paths)

            output_path = os.path.join(temp_dir, "output.gif")

            # ImageMagick delay is in 1/100ths of a second; OpenAPI specifies milliseconds.
            delay_cs = max(0, round(delay / 10))

            command = [
                "convert",
                "-dispose",
                "background",
                "-delay",
                str(delay_cs),
                "-loop",
                "0",
            ]

            for frame_path in frame_paths:
                command.extend(
                    [
                        "(",
                        frame_path,
                        "-resize",
                        f"{width}x{height}",
                        "-background",
                        "none",
                        "-gravity",
                        "center",
                        "-extent",
                        f"{width}x{height}",
                        ")",
                    ]
                )

            command.append(output_path)

            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                text=True,
            )

            if completed.returncode != 0:
                stderr = (completed.stderr or "").strip()
                stdout = (completed.stdout or "").strip()
                detail = stderr or stdout or "Unknown ImageMagick error."
                return error_response(f"Failed to create GIF: {detail}", 500)

            if not os.path.exists(output_path):
                return error_response("Failed to create GIF: output file was not generated.", 500)

            with open(output_path, "rb") as f:
                gif_bytes = f.read()

            return Response(content=gif_bytes, media_type="image/gif")

    except Exception as exc:
        return error_response(f"Internal server error: {str(exc)}", 500)


@app.get("/")
async def root():
    return {
        "message": "GIF Creator API is running.",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)