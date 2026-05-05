import os
import re
import subprocess
import tempfile
from typing import List

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="GIF Creator API", version="1.0.0")


class ErrorResponse(BaseModel):
    error: str


SIZE_PATTERN = re.compile(r"^\d+x\d+$")


def parse_target_size(size_str: str) -> str:
    """Validate and return the size string."""
    if not SIZE_PATTERN.match(size_str):
        raise ValueError(
            "targetSize must be in the format '<width>x<height>', e.g., '500x500'."
        )
    return size_str


@app.post(
    "/create-gif",
    responses={
        200: {"content": {"image/gif": {}}},
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def create_gif(
    images: List[UploadFile] = File(..., description="Array of images to be included in the GIF."),
    targetSize: str = Form(..., description="Target size for the GIF in pixels (width x height)."),
    delay: int = Form(10, description="Delay between frames in milliseconds."),
    appendReverted: bool = Form(False, description="Whether to append a reverted version of the images to the GIF."),
):
    # Validate targetSize
    try:
        target_size = parse_target_size(targetSize)
    except ValueError as ve:
        return JSONResponse(status_code=400, content={"error": str(ve)})

    if delay < 0:
        return JSONResponse(status_code=400, content={"error": "Delay must be non‑negative."})

    # Convert delay from milliseconds to ImageMagick centiseconds (1/100 sec)
    delay_cs = max(1, delay // 10)  # ImageMagick expects integer centiseconds

    # Process files in a temporary directory
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            saved_paths = []
            for idx, upload in enumerate(images):
                # Preserve original extension if possible; default to .png
                _, ext = os.path.splitext(upload.filename)
                ext = ext if ext else ".png"
                safe_name = f"frame_{idx}{ext}"
                file_path = os.path.join(tmpdir, safe_name)

                # Write file content to disk
                content = await upload.read()
                with open(file_path, "wb") as f:
                    f.write(content)

                saved_paths.append(file_path)

            # Build the sequence of frames for ImageMagick
            frame_sequence = saved_paths.copy()
            if appendReverted:
                frame_sequence += list(reversed(saved_paths))

            output_path = os.path.join(tmpdir, "result.gif")

            # Build ImageMagick command
            cmd = [
                "convert",
                "-delay",
                str(delay_cs),
                "-loop",
                "0",
                *frame_sequence,
                "-resize",
                f"{target_size}!",
                output_path,
            ]

            # Execute the command
            result = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
            )
            if result.returncode != 0:
                error_msg = result.stderr.decode().strip() or "ImageMagick conversion failed."
                raise RuntimeError(error_msg)

            # Read the generated GIF
            with open(output_path, "rb") as f:
                gif_bytes = f.read()

            return Response(content=gif_bytes, media_type="image/gif")
    except RuntimeError as re:
        return JSONResponse(status_code=500, content={"error": str(re)})
    except Exception as e:
        # Unexpected errors
        return JSONResponse(status_code=500, content={"error": f"Unexpected error: {str(e)}"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)