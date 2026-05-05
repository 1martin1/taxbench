from fastapi import FastAPI, File, UploadFile, Form, Response, HTTPException
import subprocess
import tempfile
import os
import re
import asyncio
from typing import List

app = FastAPI()

# Security and resource limits
MAX_IMAGES = 100
MAX_TOTAL_SIZE = 100 * 1024 * 1024  # 100MB
MAX_DIMENSION = 4096  # pixels
MAX_DELAY = 5000  # 5 seconds

@app.post("/create-gif")
async def create_gif(
    images: List[UploadFile] = File(...),
    targetSize: str = Form(...),
    delay: int = Form(10),
    appendReverted: bool = Form(False)
):
    # Validate image count
    if len(images) > MAX_IMAGES:
        raise HTTPException(status_code=400, detail=f"Too many images. Maximum allowed: {MAX_IMAGES}")
    
    # Validate targetSize format
    if not re.fullmatch(r"^\d+x\d+$", targetSize):
        raise HTTPException(status_code=400, detail="Invalid targetSize format. Use 'WIDTHxHEIGHT'")
    
    try:
        width, height = map(int, targetSize.split("x"))
        if width <= 0 or height <= 0:
            raise HTTPException(status_code=400, detail="Width and height must be positive integers")
        if width > MAX_DIMENSION or height > MAX_DIMENSION:
            raise HTTPException(status_code=400, detail=f"Maximum dimension allowed: {MAX_DIMENSION} pixels")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid targetSize format. Use 'WIDTHxHEIGHT' with integers.")
    
    # Validate delay
    if delay < 0 or delay > MAX_DELAY:
        raise HTTPException(status_code=400, detail=f"Delay must be between 0 and {MAX_DELAY} milliseconds")

    with tempfile.TemporaryDirectory() as temp_dir:
        image_paths = []
        total_size = 0
        
        for i, image in enumerate(images):
            try:
                contents = await image.read()
                if not contents:
                    raise HTTPException(status_code=400, detail="Empty image file")
                total_size += len(contents)
                if total_size > MAX_TOTAL_SIZE:
                    raise HTTPException(status_code=400, detail=f"Total image size exceeds {MAX_TOTAL_SIZE/1024/1024}MB limit")
            except Exception:
                raise HTTPException(status_code=400, detail="Error reading image file")
            
            ext = image.filename.split(".")[-1].lower() if "." in image.filename else "png"
            temp_path = os.path.join(temp_dir, f"img_{i}.{ext}")
            with open(temp_path, "wb") as f:
                f.write(contents)
            image_paths.append(temp_path)
        
        if not image_paths:
            raise HTTPException(status_code=400, detail="No images provided")
        
        # Check if appending reversed images would exceed image limit
        original_count = len(image_paths)
        if appendReverted:
            if original_count * 2 > MAX_IMAGES:
                raise HTTPException(status_code=400, detail="Too many images after appending reversed")
            image_paths.extend(reversed(image_paths))
        
        output_path = os.path.join(temp_dir, "output.gif")
        
        # Build ImageMagick command with validated parameters
        command = [
            "convert",
            "-resize", f"{width}x{height}",
            *image_paths,
            "-delay", str(delay),
            "-loop", "0",
            output_path
        ]
        
        try:
            # Run with timeout and capture output
            subprocess.run(command, check=True, capture_output=True, timeout=30)
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            raise HTTPException(status_code=500, detail="Failed to create GIF with ImageMagick")
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=500, detail="GIF creation timed out")
        
        if not os.path.exists(output_path):
            raise HTTPException(status_code=500, detail="GIF creation failed; output file not found")
        
        with open(output_path, "rb") as f:
            return Response(content=f.read(), media_type="image/gif")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)