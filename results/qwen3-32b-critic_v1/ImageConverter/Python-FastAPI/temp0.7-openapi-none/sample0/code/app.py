from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Response
import subprocess
import tempfile
import os
import shutil

app = FastAPI()

# Constants for security and validation
MAX_IMAGES = 100
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB
MAX_TOTAL_SIZE = 100 * 1024 * 1024  # 100MB
MAX_DIMENSION = 8192
MIN_DELAY = 0
MAX_DELAY = 10000

# Check if ImageMagick is available at startup
try:
    subprocess.run(["convert", "-version"], 
                  stdout=subprocess.DEVNULL, 
                  stderr=subprocess.DEVNULL, 
                  check=True)
except (FileNotFoundError, subprocess.CalledProcessError):
    raise RuntimeError("ImageMagick's convert command is required but not found. Install with: apt-get install imagemagick -y")

@app.post("/create-gif")
async def create_gif(
    images: list[UploadFile] = File(...),
    targetSize: str = Form(...),
    delay: int = Form(10),
    appendReverted: bool = Form(False)
):
    # Validate number of images
    if len(images) > MAX_IMAGES:
        raise HTTPException(status_code=400, 
                          detail=f"Too many images. Maximum {MAX_IMAGES} allowed.")
    
    # Validate image sizes
    total_size = 0
    for image in images:
        if image.size > MAX_IMAGE_SIZE:
            raise HTTPException(status_code=400,
                              detail=f"Image size exceeds {MAX_IMAGE_SIZE/1024/1024}MB limit.")
        total_size += image.size
        if total_size > MAX_TOTAL_SIZE:
            raise HTTPException(status_code=400,
                              detail=f"Total size exceeds {MAX_TOTAL_SIZE/1024/1024}MB limit.")
    
    # Parse and validate targetSize
    try:
        width, height = map(int, targetSize.split('x'))
        if not (1 <= width <= MAX_DIMENSION and 1 <= height <= MAX_DIMENSION):
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=400,
                          detail=f"Invalid targetSize. Must be between 1-{MAX_DIMENSION} pixels.")
    
    # Validate delay
    if not (MIN_DELAY <= delay <= MAX_DELAY):
        raise HTTPException(status_code=400,
                          detail=f"Delay must be between {MIN_DELAY} and {MAX_DELAY} milliseconds.")
    
    # Save images to temp files
    temp_files = []
    try:
        for image in images:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".tmp") as tmp:
                shutil.copyfileobj(image.file, tmp)
                temp_files.append(tmp.name)
    except Exception as e:
        for f in temp_files:
            os.remove(f)
        raise HTTPException(status_code=500, detail="Error saving uploaded images.")
    
    # Handle appendReverted
    if appendReverted:
        temp_files += temp_files[::-1]
    
    # Create output temp file
    try:
        output_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".gif")
        output_path = output_temp.name
        output_temp.close()
    except Exception as e:
        for f in temp_files:
            os.remove(f)
        raise HTTPException(status_code=500, detail="Failed to create output file.")
    
    # Prepare the convert command
    resize_arg = f"{width}x{height}"
    delay_arg = delay // 10  # Convert milliseconds to hundredths of a second
    command = ["convert", "-resize", resize_arg, "-delay", str(delay_arg)] + temp_files + [output_path]
    
    try:
        subprocess.run(command, check=True, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        for f in temp_files:
            os.remove(f)
        os.remove(output_path)
        raise HTTPException(status_code=500, detail="GIF creation failed: ImageMagick error.")
    except Exception as e:
        for f in temp_files:
            os.remove(f)
        os.remove(output_path)
        raise HTTPException(status_code=500, detail="Unexpected error during GIF creation.")
    
    # Read the output file
    try:
        with open(output_path, "rb") as f:
            gif_data = f.read()
    except Exception as e:
        for f in temp_files:
            os.remove(f)
        os.remove(output_path)
        raise HTTPException(status_code=500, detail="Failed to read generated GIF.")
    
    # Clean up
    for f in temp_files:
        os.remove(f)
    os.remove(output_path)
    
    return Response(content=gif_data, media_type="image/gif")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)