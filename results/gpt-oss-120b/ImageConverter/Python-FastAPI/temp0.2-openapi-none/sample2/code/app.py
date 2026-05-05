import os
import subprocess
import tempfile
from typing import List

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import JSONResponse, Response

app = FastAPI()


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"error": str(exc)})


@app.post("/create-gif")
async def create_gif(
    images: List[UploadFile] = File(..., description="Array of images to be included in the GIF."),
    targetSize: str = Form(..., description="Target size for the GIF in pixels (width x height)."),
    delay: int = Form(10, description="Delay between frames in milliseconds."),
    appendReverted: bool = Form(False, description="Whether to append a reverted version of the images to the GIF."),
):
    # Validate targetSize format (e.g., "500x500")
    if "x" not in targetSize.lower():
        raise HTTPException(status_code=400, detail="Invalid targetSize format. Expected WIDTHxHEIGHT.")
    try:
        width_str, height_str = targetSize.lower().split("x")
        width = int(width_str)
        height = int(height_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid targetSize numbers.")

    if width <= 0 or height <= 0:
        raise HTTPException(status_code=400, detail="Width and height must be positive integers.")

    if delay < 0:
        raise HTTPException(status_code=400, detail="Delay must be non‑negative.")

    # ImageMagick expects delay in centiseconds (1/100 sec)
    delay_cs = max(1, int(delay / 10))

    # Save uploaded images to a temporary directory
    with tempfile.TemporaryDirectory() as tmpdir:
        saved_paths: List[str] = []
        for idx, upload in enumerate(images):
            # Preserve original extension if possible, default to .png
            _, ext = os.path.splitext(upload.filename)
            ext = ext if ext else ".png"
            safe_name = f"frame_{idx}{ext}"
            file_path = os.path.join(tmpdir, safe_name)

            content = await upload.read()
            if not content:
                raise HTTPException(status_code=400, detail=f"File {upload.filename} is empty.")
            with open(file_path, "wb") as f:
                f.write(content)

            saved_paths.append(file_path)

        if not saved_paths:
            raise HTTPException(status_code=400, detail="No images provided.")

        # Optionally append the reversed sequence
        if appendReverted:
            saved_paths += list(reversed(saved_paths))

        output_path = os.path.join(tmpdir, "result.gif")

        # Build ImageMagick command
        cmd = [
            "convert",
            "-delay",
            str(delay_cs),
            "-loop",
            "0",
        ] + saved_paths + [
            "-resize",
            f"{width}x{height}!",
            output_path,
        ]

        try:
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.decode().strip() or "Unknown ImageMagick error."
            raise HTTPException(status_code=500, detail=f"ImageMagick error: {err_msg}")

        # Read the generated GIF and return it
        try:
            with open(output_path, "rb") as f:
                gif_bytes = f.read()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read generated GIF: {str(e)}")

    return Response(content=gif_bytes, media_type="image/gif")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)