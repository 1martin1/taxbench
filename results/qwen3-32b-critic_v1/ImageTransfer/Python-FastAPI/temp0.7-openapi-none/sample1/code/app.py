import os
import uuid
import errno
from fastapi import FastAPI, UploadFile, File, HTTPException, Response
import shutil

app = FastAPI()

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Configuration
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp', '.svg'}
EXT_TO_MIME = {
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.gif': 'image/gif',
    '.bmp': 'image/bmp',
    '.tiff': 'image/tiff',
    '.tif': 'image/tiff',
    '.webp': 'image/webp',
    '.svg': 'image/svg+xml',
}

@app.post("/upload")
async def upload_image(file: UploadFile = File(...)):
    # Validate filename
    filename = file.filename
    if not filename:
        raise HTTPException(status_code=400, detail="No filename provided")
    
    # Validate file extension
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Invalid file extension")
    
    # Validate file size
    if file.size > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large")
    
    # Determine MIME type based on extension
    content_type = EXT_TO_MIME[ext]
    
    # Generate unique ID and paths
    file_id = str(uuid.uuid4())
    file_path = os.path.join(UPLOAD_DIR, file_id)
    content_type_path = os.path.join(UPLOAD_DIR, f"{file_id}.content_type")
    
    try:
        # Write file and content type
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        with open(content_type_path, "w") as ct_file:
            ct_file.write(content_type)
    except OSError as e:
        if e.errno == errno.ENOSPC:
            raise HTTPException(status_code=413, detail="Disk space full")
        raise HTTPException(status_code=500, detail="Internal server error")
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")
    
    return {"id": file_id}

@app.get("/images/{imageId}")
async def get_image(imageId: str):
    file_path = os.path.join(UPLOAD_DIR, imageId)
    content_type_path = os.path.join(UPLOAD_DIR, f"{imageId}.content_type")
    
    if not (os.path.exists(file_path) and os.path.exists(content_type_path)):
        raise HTTPException(status_code=404, detail="Image not found")
    
    try:
        with open(content_type_path, "r") as ct_file:
            content_type = ct_file.read().strip()
        with open(file_path, "rb") as f:
            content = f.read()
        return Response(content=content, media_type=content_type, headers={"Content-Disposition": "inline"})
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    config = uvicorn.Config(app, host="0.0.0.0", port=5000, max_request_size=MAX_FILE_SIZE)
    server = uvicorn.Server(config)
    server.run()