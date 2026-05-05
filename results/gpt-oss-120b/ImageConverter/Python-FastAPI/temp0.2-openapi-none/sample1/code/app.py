import os
import subprocess
import tempfile
from typing import List

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

app = FastAPI(title="GIF Creator API", version="1.0.0")


def _parse_target_size(size_str: str):
    """Validate and return (width, height) tuple."""
    if "x" not in size_str:
        raise ValueError("targetSize must be in WIDTHxHEIGHT format.")
    w, h = size_str.split("x", 1)
    if not (w.isdigit() and h.isdigit()):
        raise ValueError("targetSize dimensions must be integers.")
    return w, h


@app.post("/create-gif", responses={
    200: {"content": {"image/gif": {}}},
    400: {"content": {"application/json": {}}},
    500: {"content": {"application/json": {}}}
})
async def create_gif(
    images: List[UploadFile] = File(..., description="Array of images to be included in the GIF."),
    targetSize: str = Form(..., description="Target size for the GIF in pixels (width x height)."),
    delay: int = Form(10, description="Delay between frames in milliseconds."),
    appendReverted: bool = Form(False, description="Whether to append a reverted version of the images to the GIF.")
):
    # Validate targetSize
    try:
        _parse_target_size(targetSize)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    if delay < 0:
        raise HTTPException(status_code=400, detail="Delay must be non‑negative.")

    # Convert delay from milliseconds to ImageMagick centiseconds (1/100 sec)
    delay_cs = max(1, delay // 10)  # ImageMagick requires at least 1 centisecond

    # Save uploaded files to a temporary directory
    with tempfile.TemporaryDirectory() as tmpdir:
        input_paths = []
        for idx, upload in enumerate(images):
            # Preserve original extension if possible, fallback to .png
            _, ext = os.path.splitext(upload.filename)
            ext = ext if ext else ".png"
            file_path = os.path.join(tmpdir, f"frame_{idx}{ext}")

            try:
                content = await upload.read()
                with open(file_path, "wb") as f:
                    f.write(content)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to write uploaded image: {e}")

            input_paths.append(file_path)

        if not input_paths:
            raise HTTPException(status_code=400, detail="No images were provided.")

        # Build the list of frames for ImageMagick
        frames = input_paths.copy()
        if appendReverted:
            frames += list(reversed(input_paths))

        output_path = os.path.join(tmpdir, "output.gif")

        # Build ImageMagick command
        cmd = [
            "convert",
            "-delay", str(delay_cs),
            "-loop", "0",
            "-resize", targetSize,
            *frames,
            output_path
        ]

        try:
            result = subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=tmpdir,
            )
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.decode().strip() or "Unknown ImageMagick error."
            raise HTTPException(status_code=500, detail=f"ImageMagick failed: {err_msg}")

        # Read generated GIF
        try:
            with open(output_path, "rb") as f:
                gif_bytes = f.read()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read generated GIF: {e}")

    return Response(content=gif_bytes, media_type="image/gif")


# Custom error handler to match the OpenAPI spec (error field)
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail}
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=5000, log_level="info")