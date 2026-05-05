from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Response
import shutil
import tempfile
import os
import subprocess
import re
from typing import List

app = FastAPI()

MAX_IMAGES = 100
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB
MAX_DIMENSION = 4096

@app.post("/create-gif")
async def create_gif(
    images: List[UploadFile] = File(...),
    targetSize: str = Form(...),
    delay: int = Form(10),
    appendReverted: bool = Form(False)
):
    # Validate number of images
    if len(images) > MAX_IMAGES:
        raise HTTPException(status_code=400, detail=f"Too many images. Maximum {MAX_IMAGES} allowed.")
    
    # Validate delay
    if delay < 0:
        raise HTTPException(status_code=400, detail="Delay must be a non-negative integer.")
    
    # Validate targetSize format
    if not re.fullmatch(r'^\d+x\d+$', targetSize):
        raise HTTPException(status_code=400, detail="Invalid targetSize format. Use 'WxH' with positive integers.")
    
    try:
        width, height = map(int, targetSize.split("x"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid targetSize values.")
    
    if not (1 <= width <= MAX_DIMENSION and 1 <= height <= MAX_DIMENSION):
        raise HTTPException(
            status_code=400, 
            detail=f"Width and height must be between 1 and {MAX_DIMENSION} pixels."
        )
    
    with tempfile.TemporaryDirectory() as temp_dir:
        image_paths = []
        for idx, image in enumerate(images):
            # Validate image size
            if image.size > MAX_IMAGE_SIZE:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Image {idx} exceeds maximum size of {MAX_IMAGE_SIZE / 1024 / 1024}MB."
                )
            
            file_extension = os.path.splitext(image.filename)[1] or ".tmp"
            file_path = os.path.join(temp_dir, f"img{idx}{file_extension}")
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(image.file, buffer)
            image_paths.append(file_path)
        
        if appendReverted:
            image_paths += image_paths[::-1]
        
        output_path = os.path.join(temp_dir, "output.gif")
        resize_arg = f"{width}x{height}"
        delay_value = delay // 10  # Convert milliseconds to centiseconds (hundredths of a second)
        
        cmd = ["convert", "-resize", resize_arg, "-delay", str(delay_value), *image_paths, output_path]
        
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as e:
            error_message = e.stderr.decode().strip() or "Image conversion failed"
            raise HTTPException(status_code=500, detail=error_message)
        
        with open(output_path, "rb") as f:
            gif_data = f.read()
        
        return Response(content=gif_data, media_type="image/gif")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)