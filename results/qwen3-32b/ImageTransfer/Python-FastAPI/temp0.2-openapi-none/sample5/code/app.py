from fastapi import FastAPI, File, UploadFile, HTTPException, Path
from fastapi.responses import FileResponse
import uuid
import os

app = FastAPI()

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

def is_valid_image(data: bytes) -> bool:
    """Validate image file content by magic number."""
    if data.startswith(b'\xFF\xD8\xFF'):  # JPEG
        return True
    if data.startswith(b'\x89PNG\r\n\x1a\n'):  # PNG
        return True
    if data.startswith(b'GIF87a') or data.startswith(b'GIF89a'):  # GIF
        return True
    if data.startswith(b'BM'):  # BMP
        return True
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP':  # WEBP
        return True
    return False

@app.post("/upload")
async def upload_image(file: UploadFile = File(...)):
    try:
        # Validate content type
        if not file.content_type.startswith('image/'):
            raise HTTPException(status_code=400, detail="Invalid content type")
        
        # Validate file size
        if file.size > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail="File too large")
        
        # Validate file content
        first_chunk = await file.read(1024)
        if not is_valid_image(first_chunk):
            raise HTTPException(status_code=400, detail="Invalid image format")
        
        # Write file in chunks to avoid memory issues
        image_id = str(uuid.uuid4())
        file_path = os.path.join(UPLOAD_DIR, image_id)
        meta_path = os.path.join(UPLOAD_DIR, f"{image_id}.meta")
        
        with open(file_path, "wb") as buffer:
            buffer.write(first_chunk)
            while chunk := await file.read(1024 * 1024):
                buffer.write(chunk)
        
        # Store content type in meta file
        with open(meta_path, "w") as f:
            f.write(file.content_type)
        
        return {"id": image_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/images/{image_id}")
async def get_image(image_id: str = Path(..., description="The ID of the image to be viewed.")):
    file_path = os.path.join(UPLOAD_DIR, image_id)
    meta_path = os.path.join(UPLOAD_DIR, f"{image_id}.meta")
    
    if not (os.path.exists(file_path) and os.path.exists(meta_path)):
        raise HTTPException(status_code=404, detail="Image not found")
    
    try:
        with open(meta_path, "r") as f:
            content_type = f.read().strip()
        return FileResponse(file_path, media_type=content_type)
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)