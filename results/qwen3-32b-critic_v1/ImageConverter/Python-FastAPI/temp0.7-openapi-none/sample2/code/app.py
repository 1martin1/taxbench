from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Response
import subprocess
import tempfile
import os
from typing import List
import re

app = FastAPI()

MAX_IMAGES = 50
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB
ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp'}
MAX_TOTAL_FRAMES = 100
MAX_TARGET_DIM = 4096  # Prevent excessively large images

@app.post("/create-gif")
async def create_gif(
    images: List[UploadFile] = File(...),
    targetSize: str = Form(...),
    delay: int = Form(10),
    appendReverted: bool = Form(False)
):
    # Validate delay
    if delay <= 0:
        raise HTTPException(status_code=400, detail="Delay must be a positive integer.")
    
    # Validate targetSize format
    if not re.fullmatch(r'\d+x\d+', targetSize):
        raise HTTPException(status_code=400, detail="Invalid targetSize format. Use 'widthxheight' with positive integers.")
    
    try:
        width, height = map(int, targetSize.split('x'))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid targetSize format. Use 'widthxheight' with positive integers.")
    
    if width <= 0 or height <= 0:
        raise HTTPException(status_code=400, detail="Width and height must be positive integers.")
    
    if width > MAX_TARGET_DIM or height > MAX_TARGET_DIM:
        raise HTTPException(status_code=400, detail=f"Width and height must not exceed {MAX_TARGET_DIM} pixels.")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        image_paths = []
        for idx, image in enumerate(images):
            # Check number of images
            if len(image_paths) >= MAX_IMAGES:
                raise HTTPException(status_code=400, detail=f"Maximum {MAX_IMAGES} images allowed.")
            
            # Read and validate file
            try:
                contents = await image.read()
                if not contents:
                    raise HTTPException(status_code=400, detail="Uploaded file is empty.")
                if len(contents) > MAX_IMAGE_SIZE:
                    raise HTTPException(status_code=400, detail=f"Image size exceeds {MAX_IMAGE_SIZE / 1024 / 1024}MB limit.")
                
                original_filename = image.filename or "image"
                ext = os.path.splitext(original_filename)[1].lower()
                if ext not in ALLOWED_EXTENSIONS:
                    raise HTTPException(status_code=400, detail=f"Unsupported file type. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}")
                
                image_path = os.path.join(temp_dir, f"image_{idx}{ext}")
                with open(image_path, "wb") as f:
                    f.write(contents)
                image_paths.append(image_path)
            except HTTPException:
                raise
            except Exception as e:
                # Catch other exceptions and convert to HTTPException
                raise HTTPException(status_code=400, detail="Error processing uploaded image.") from e
        
        # Check if appending reverted would exceed frame limit
        if appendReverted:
            new_length = len(image_paths) * 2
            if new_length > MAX_TOTAL_FRAMES:
                raise HTTPException(status_code=400, detail=f"Total frames after appending reverted would exceed {MAX_TOTAL_FRAMES} limit.")
            image_paths += image_paths[::-1]
        elif len(image_paths) > MAX_TOTAL_FRAMES:
            raise HTTPException(status_code=400, detail=f"Maximum {MAX_TOTAL_FRAMES} frames allowed.")
        
        output_path = os.path.join(temp_dir, "output.gif")
        delay_value = delay // 10  # Convert ms to hundredths of a second
        
        command = [
            'convert',
            '-delay', str(delay_value),
            '-resize', f"{width}x{height}!",
            *image_paths,
            output_path
        ]
        
        try:
            result = subprocess.run(
                command,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30  # Add timeout
            )
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.decode() if e.stderr else "Unknown error"
            raise HTTPException(status_code=500, detail=f"ImageMagick error: {error_msg}")
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=500, detail="GIF creation timed out.")
        
        try:
            with open(output_path, "rb") as f:
                gif_data = f.read()
            if not gif_data:
                raise HTTPException(status_code=500, detail="Generated GIF is empty.")
            return Response(content=gif_data, media_type="image/gif")
        except Exception as e:
            raise HTTPException(status_code=500, detail="Failed to read generated GIF.") from e

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)