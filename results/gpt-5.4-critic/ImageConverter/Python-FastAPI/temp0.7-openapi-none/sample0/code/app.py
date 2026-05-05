import os
import re
import shutil
import subprocess
import tempfile
from typing import Annotated, List, Optional

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

DEFAULT_DELAY_MS = 10
MAX_IMAGES = 50
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_DIMENSION = 2000
MAX_OUTPUT_GIF_BYTES = 50 * 1024 * 1024
CONVERT_TIMEOUT_SECONDS = 30
MAX_PIXELS = MAX_DIMENSION * MAX_DIMENSION


def parse_target_size(value: str) -> tuple[int, int]:
    match = TARGET_SIZE_PATTERN.match(value or "")
    if not match:
        raise ValueError("targetSize must be in the format WIDTHxHEIGHT, e.g. 500x500")
    width = int(match.group(1))
    height = int(match.group(2))
    if width <= 0 or height <= 0:
        raise ValueError("targetSize width and height must be positive integers")
    if width > MAX_DIMENSION or height > MAX_DIMENSION:
        raise ValueError(f"targetSize width and height must not exceed {MAX_DIMENSION}")
    return width, height


def validate_delay(value: int) -> int:
    if value < 0:
        raise ValueError("delay must be greater than or equal to 0")
    return value


def ensure_convert_available() -> None:
    if shutil.which("convert") is None:
        raise RuntimeError("Image conversion backend is unavailable")


def ensure_multipart_available() -> None:
    try:
        import multipart  # noqa: F401
    except Exception as exc:
        raise RuntimeError("Multipart form support is unavailable") from exc


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content={"error": "Invalid request"})


@app.on_event("startup")
async def startup_checks() -> None:
    ensure_multipart_available()
    ensure_convert_available()


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
    delay: Annotated[Optional[str], Form(description="Delay between frames in milliseconds.")] = None,
    appendReverted: bool = Form(False, description="Whether to append a reverted version of the images to the GIF."),
):
    opened_files: List[UploadFile] = []
    try:
        if not images:
            return JSONResponse(status_code=400, content={"error": "At least one image must be provided"})

        if len(images) > MAX_IMAGES:
            return JSONResponse(status_code=400, content={"error": f"No more than {MAX_IMAGES} images are allowed"})

        width, height = parse_target_size(targetSize)

        if delay is None or delay == "":
            validated_delay = DEFAULT_DELAY_MS
        else:
            try:
                validated_delay = validate_delay(int(delay))
            except (TypeError, ValueError):
                return JSONResponse(status_code=400, content={"error": "delay must be an integer greater than or equal to 0"})

        ensure_convert_available()

        with tempfile.TemporaryDirectory() as temp_dir:
            input_paths: List[str] = []
            total_uploaded_bytes = 0

            for index, image in enumerate(images):
                opened_files.append(image)

                if not image.filename:
                    return JSONResponse(status_code=400, content={"error": f"Image at index {index} is missing a filename"})

                destination_path = os.path.join(temp_dir, f"input_{index}_{os.path.basename(image.filename)}")

                written_for_file = 0
                with open(destination_path, "wb") as output_file:
                    while True:
                        chunk = await image.read(1024 * 1024)
                        if not chunk:
                            break
                        chunk_len = len(chunk)
                        written_for_file += chunk_len
                        total_uploaded_bytes += chunk_len

                        if written_for_file > MAX_FILE_SIZE_BYTES:
                            return JSONResponse(
                                status_code=400,
                                content={"error": f"Image '{image.filename}' exceeds the maximum allowed size"},
                            )

                        if total_uploaded_bytes > MAX_TOTAL_UPLOAD_BYTES:
                            return JSONResponse(
                                status_code=400,
                                content={"error": "Total upload size exceeds the maximum allowed size"},
                            )

                        output_file.write(chunk)

                if written_for_file == 0:
                    return JSONResponse(status_code=400, content={"error": f"Image '{image.filename}' is empty"})

                input_paths.append(destination_path)

            ordered_paths = list(input_paths)
            if appendReverted and len(input_paths) > 1:
                ordered_paths.extend(reversed(input_paths))

            output_path = os.path.join(temp_dir, "output.gif")

            if validated_delay == 0:
                imagemagick_delay = 0
            else:
                imagemagick_delay = max(1, round(validated_delay / 10))

            command = [
                "convert",
                "-limit",
                "memory",
                "256MiB",
                "-limit",
                "map",
                "256MiB",
                "-limit",
                "disk",
                "512MiB",
                "-limit",
                "area",
                str(MAX_PIXELS),
                "-delay",
                str(imagemagick_delay),
                "-dispose",
                "previous",
            ]

            for path in ordered_paths:
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
                    output_path,
                ]
            )

            try:
                completed = subprocess.run(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=CONVERT_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                return JSONResponse(status_code=500, content={"error": "Internal Server Error"})

            if completed.returncode != 0:
                return JSONResponse(status_code=500, content={"error": "Internal Server Error"})

            if not os.path.exists(output_path):
                return JSONResponse(status_code=500, content={"error": "Internal Server Error"})

            output_size = os.path.getsize(output_path)
            if output_size <= 0:
                return JSONResponse(status_code=500, content={"error": "Internal Server Error"})
            if output_size > MAX_OUTPUT_GIF_BYTES:
                return JSONResponse(status_code=500, content={"error": "Internal Server Error"})

            with open(output_path, "rb") as gif_file:
                gif_bytes = gif_file.read(MAX_OUTPUT_GIF_BYTES + 1)

            if len(gif_bytes) == 0 or len(gif_bytes) > MAX_OUTPUT_GIF_BYTES:
                return JSONResponse(status_code=500, content={"error": "Internal Server Error"})

            return Response(content=gif_bytes, media_type="image/gif")

    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except RuntimeError:
        return JSONResponse(status_code=500, content={"error": "Internal Server Error"})
    except Exception:
        return JSONResponse(status_code=500, content={"error": "Internal Server Error"})
    finally:
        for image in opened_files:
            try:
                await image.close()
            except Exception:
                pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)