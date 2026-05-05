import os
import shutil
import subprocess
import tempfile
from typing import List

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Response, status

app = FastAPI(title="GIF Creator API", description="API to create a GIF from a set of images with specified parameters.", version="1.0.0")


def parse_target_size(size_str: str):
    """
    Parse a size string like '500x500' into (width, height).
    """
    try:
        width_str, height_str = size_str.lower().split("x")
        width = int(width_str.strip())
        height = int(height_str.strip())
        if width <= 0 or height <= 0:
            raise ValueError
        return width, height
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid targetSize format. Expected format 'WIDTHxHEIGHT', e.g., '500x500'.",
        )


def ms_to_delay_units(delay_ms: int):
    """
    ImageMagick's -delay expects units of 1/100 second.
    Convert milliseconds to that unit (10 ms = 1 unit).
    """
    if delay_ms < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Delay must be a non‑negative integer.",
        )
    return max(1, delay_ms // 10)  # ensure at least 1 unit


@app.post(
    "/create-gif",
    responses={
        200: {"content": {"image/gif": {}}},
        400: {"description": "Bad Request"},
        500: {"description": "Internal Server Error"},
    },
)
async def create_gif(
    images: List[UploadFile] = File(..., description="Array of images to be included in the GIF."),
    targetSize: str = Form(..., description="Target size for the GIF in pixels (width x height)."),
    delay: int = Form(10, description="Delay between frames in milliseconds."),
    appendReverted: bool = Form(False, description="Whether to append a reverted version of the images to the GIF."),
):
    # Validate inputs
    if not images:
        raise HTTPException(status_code=400, detail="At least one image must be provided.")
    width, height = parse_target_size(targetSize)
    delay_units = ms_to_delay_units(delay)

    # Create a temporary working directory
    work_dir = tempfile.mkdtemp()
    try:
        resized_paths = []

        # Resize each uploaded image and store as a temporary file
        for idx, upload in enumerate(images):
            # Save original upload to a temp file
            original_path = os.path.join(work_dir, f"orig_{idx}")
            with open(original_path, "wb") as f:
                content = await upload.read()
                f.write(content)

            # Prepare resized file path
            resized_path = os.path.join(work_dir, f"resized_{idx}.png")  # use PNG for consistency
            # Run ImageMagick convert to resize
            resize_cmd = [
                "convert",
                original_path,
                "-resize",
                f"{width}x{height}!",
                resized_path,
            ]
            result = subprocess.run(resize_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise HTTPException(
                    status_code=500,
                    detail=f"Image resizing failed: {result.stderr.strip()}",
                )
            resized_paths.append(resized_path)

        # Optionally append reversed sequence
        final_sequence = resized_paths.copy()
        if appendReverted:
            final_sequence += list(reversed(resized_paths))

        # Output GIF path
        output_gif_path = os.path.join(work_dir, "output.gif")

        # Build the convert command for GIF creation
        gif_cmd = ["convert", "-delay", str(delay_units), "-loop", "0"]
        gif_cmd.extend(final_sequence)
        gif_cmd.append(output_gif_path)

        result = subprocess.run(gif_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"GIF creation failed: {result.stderr.strip()}",
            )

        # Read the generated GIF
        with open(output_gif_path, "rb") as f:
            gif_bytes = f.read()

        return Response(content=gif_bytes, media_type="image/gif")
    finally:
        # Clean up temporary files
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)