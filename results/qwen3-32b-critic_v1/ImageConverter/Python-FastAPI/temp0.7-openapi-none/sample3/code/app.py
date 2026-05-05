import subprocess
from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Response
from typing import List
import os
import shutil
from tempfile import TemporaryDirectory
import logging

app = FastAPI()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

@app.on_event("startup")
async def check_imagemagick():
    try:
        subprocess.run(["convert", "-version"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.critical("ImageMagick 'convert' command is not available: %s", e)
        raise RuntimeError("ImageMagick 'convert' command is required but not available.") from e

@app.post("/create-gif")
async def create_gif(
    images: List[UploadFile] = File(...),
    targetSize: str = Form(...),
    delay: int = Form(10),
    appendReverted: bool = Form(False)
):
    try:
        # Validate image count
        if len(images) > 100:
            raise HTTPException(status_code=400, detail="Too many images. Maximum allowed is 100.")
            
        # Validate targetSize format
        parts = targetSize.split("x")
        if len(parts) != 2:
            raise ValueError("targetSize must be in WxH format")
            
        width, height = parts
        if not (width.isdigit() and height.isdigit()):
            raise ValueError("Width and height must be integers")
            
        width_int = int(width)
        height_int = int(height)
        if width_int <= 0 or height_int <= 0:
            raise ValueError("Dimensions must be positive integers")
        if width_int > 4096 or height_int > 4096:
            raise ValueError("Maximum allowed size is 4096x4096")
            
        with TemporaryDirectory() as temp_dir:
            image_paths = []
            for idx, image in enumerate(images):
                ext = os.path.splitext(image.filename)[1].lower() or ".jpg"
                temp_image_path = os.path.join(temp_dir, f"img_{idx}{ext}")
                with open(temp_image_path, "wb") as buffer:
                    shutil.copyfileobj(image.file, buffer)
                image_paths.append(temp_image_path)
            
            if appendReverted:
                image_paths += image_paths[::-1]
            
            output_gif_path = os.path.join(temp_dir, "output.gif")
            
            # Build convert command
            command = [
                "convert",
                "-resize", f"{width_int}x{height_int}",
                "-delay", str(delay),
                "-loop", "0",
                *image_paths,
                output_gif_path
            ]
            
            # Execute command
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode != 0:
                logger.error("ImageMagick command failed: %s", result.stderr)
                raise HTTPException(status_code=500, detail="Failed to generate GIF with ImageMagick.")
            
            # Read GIF data
            with open(output_gif_path, "rb") as f:
                gif_data = f.read()
            
            return Response(content=gif_data, media_type="image/gif")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error during GIF creation.")
        raise HTTPException(status_code=500, detail="An internal server error occurred.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)