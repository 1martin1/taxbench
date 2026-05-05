import uuid
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import FileResponse
import os
from pathlib import Path

app = FastAPI()

# Create uploads directory if it doesn't exist
os.makedirs("uploads", exist_ok=True)

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

def is_image_file(file_path: Path) -> bool:
    """Check if a file is a valid image by reading its header bytes."""
    try:
        with file_path.open('rb') as f:
            header = f.read(32)
        if not header:
            return False  # Empty file
            
        # Check for common image headers
        if header.startswith(b'\xFF\xD8\xFF'):  # JPEG
            return True
        if header.startswith(b'\x89PNG\r\n\x1a\n'):  # PNG
            return True
        if header.startswith(b'GIF87a') or header.startswith(b'GIF89a'):  # GIF
            return True
        if header.startswith(b'BM'):  # BMP
            return True
        if header.startswith(b'RIFF') and header[8:12] == b'WEBP':  # WEBP
            return True
            
        return False
    except Exception:
        return False

def get_mime_type(file_path: Path) -> str:
    """Determine the correct MIME type based on file content."""
    try:
        with file_path.open('rb') as f:
            header = f.read(32)
        if not header:
            return 'application/octet-stream'
            
        if header.startswith(b'\xFF\xD8\xFF'):  # JPEG
            return 'image/jpeg'
        if header.startswith(b'\x89PNG\r\n\x1a\n'):  # PNG
            return 'image/png'
        if header.startswith(b'GIF87a') or header.startswith(b'GIF89a'):  # GIF
            return 'image/gif'
        if header.startswith(b'BM'):  # BMP
            return 'image/bmp'
        if header.startswith(b'RIFF') and header[8:12] == b'WEBP':  # WEBP
            return 'image/webp'
            
        return 'application/octet-stream'
    except Exception:
        return 'application/octet-stream'

@app.post("/upload")
async def upload_image(file: UploadFile = File(...)):
    try:
        # Generate unique ID for the image
        image_id = uuid.uuid4().hex
        
        # Extract file extension from original filename
        original_filename = file.filename
        if not original_filename:
            raise HTTPException(status_code=400, detail="Invalid filename")
            
        file_extension = Path(original_filename).suffix
        
        # Create new filename with unique ID and original extension
        new_filename = f"{image_id}{file_extension}"
        file_path = Path("uploads") / new_filename
        
        total_size = 0
        chunk_size = 1024  # 1KB
        
        try:
            with file_path.open("wb") as buffer:
                while True:
                    chunk = await file.read(chunk_size)
                    if not chunk:
                        break
                        
                    if total_size + len(chunk) > MAX_FILE_SIZE:
                        buffer.close()
                        file_path.unlink(missing_ok=True)
                        raise HTTPException(status_code=400, detail="File too large")
                        
                    buffer.write(chunk)
                    total_size += len(chunk)
        except Exception:
            file_path.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail="Internal server error")
        
        # Validate the file is actually an image
        if not is_image_file(file_path):
            file_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="Invalid image format")
            
        return {"id": image_id}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/images/{imageId}")
async def get_image(imageId: str):
    uploads_dir = Path("uploads")
    image_file = None
    
    # Search for file matching the imageId pattern
    for file in uploads_dir.glob(f"{imageId}.*"):
        image_file = file
        break
        
    if not image_file:
        raise HTTPException(status_code=404, detail="Image not found")
    
    # Determine mime type based on file content
    mime_type = get_mime_type(image_file)
    
    # Return file response with appropriate content type and inline disposition
    return FileResponse(
        path=str(image_file),
        media_type=mime_type,
        headers={"Content-Disposition": "inline"},
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)