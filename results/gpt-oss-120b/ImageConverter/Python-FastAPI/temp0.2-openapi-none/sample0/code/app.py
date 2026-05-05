import os
import subprocess
import tempfile
from pathlib import Path
from typing import List

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Response

app = FastAPI(title="GIF Creator API", description="API to create a GIF from a set of images with specified parameters.", version="1.0.0")


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
    """
    Create a GIF from uploaded images using ImageMagick.
    """
    # Validate targetSize format (e.g., "500x500")
    try:
        width_str, height_str = targetSize.lower().split("x")
        width = int(width_str)
        height = int(height_str)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid targetSize format. Expected WIDTHxHEIGHT (e.g., 500x500).",
        )

    # Prepare temporary files for the uploaded images
    temp_input_paths: List[str] = []
    out_path: str = ""

    try:
        for img in images:
            suffix = Path(img.filename).suffix or ".png"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                content = await img.read()
                tmp.write(content)
                temp_input_paths.append(tmp.name)

        # Build the list of input files, optionally appending the reversed sequence
        input_files = temp_input_paths.copy()
        if appendReverted:
            input_files += list(reversed(temp_input_paths))

        # Create a temporary file for the output GIF
        with tempfile.NamedTemporaryFile(delete=False, suffix=".gif") as out_tmp:
            out_path = out_tmp.name

        # ImageMagick delay is expressed in 1/100ths of a second
        im_delay = max(1, int(delay / 10))

        # Build the ImageMagick command
        cmd = [
            "convert",
            "-delay",
            str(im_delay),
            "-loop",
            "0",
            "-resize",
            f"{width}x{height}!",
        ] + input_files + [out_path]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            raise RuntimeError(f"ImageMagick error: {result.stderr.strip()}")

        # Read the generated GIF into memory
        with open(out_path, "rb") as f:
            gif_bytes = f.read()

        return Response(content=gif_bytes, media_type="image/gif")

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        # Clean up temporary input files
        for path in temp_input_paths:
            try:
                os.unlink(path)
            except OSError:
                pass
        # Clean up the temporary output file
        if out_path:
            try:
                os.unlink(out_path)
            except OSError:
                pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)