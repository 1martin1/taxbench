from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
import os
import uuid
import sqlite3
import shutil

app = FastAPI()

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Initialize database
def init_db():
    conn = sqlite3.connect('images.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS images (
            id TEXT PRIMARY KEY,
            content_type TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(
        status_code=400,
        content={"error": "Invalid input"},
    )

def get_mime_type(content: bytes) -> str:
    if not content:
        raise ValueError("Empty file")
    
    if content.startswith(b'\xFF\xD8\xFF'):  # JPEG
        return 'image/jpeg'
    elif content.startswith(b'\x89PNG\r\n\x1a\n'):  # PNG
        return 'image/png'
    elif content.startswith(b'GIF87a') or content.startswith(b'GIF89a'):  # GIF
        return 'image/gif'
    elif content.startswith(b'BM'):  # BMP
        return 'image/bmp'
    elif content.startswith(b'RIFF') and content[8:12] == b'WEBP':  # WebP
        return 'image/webp'
    elif content.startswith(b'\x49\x49\x2A\x00') or content.startswith(b'\x4D\x4D\x00\x2A'):  # TIFF
        return 'image/tiff'
    elif content.startswith(b'<?xml') and b'<svg' in content:  # SVG
        return 'image/svg+xml'
    else:
        raise ValueError("Unsupported file type")

@app.post("/upload")
async def upload_image(file: UploadFile = File(...)):
    try:
        # File size validation
        if file.size > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail="File too large")
        
        # Generate unique ID
        image_id = str(uuid.uuid4().hex)
        file_path_temp = os.path.join(UPLOAD_DIR, f"{image_id}.tmp")
        file_path = os.path.join(UPLOAD_DIR, image_id)
        
        # Read and validate content
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Empty file")
        
        mime_type = get_mime_type(content)
        
        # Write to temporary file
        with open(file_path_temp, "wb") as buffer:
            buffer.write(content)
        
        # Store in database
        conn = sqlite3.connect('images.db')
        cursor = conn.cursor()
        cursor.execute("INSERT INTO images (id, content_type) VALUES (?, ?)", (image_id, mime_type))
        conn.commit()
        
        # Atomically rename file
        os.rename(file_path_temp, file_path)
        conn.close()
        
        return {"id": image_id}
        
    except Exception as e:
        # Clean up temporary file if needed
        if 'file_path_temp' in locals() and os.path.exists(file_path_temp):
            os.remove(file_path_temp)
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/images/{image_id}")
async def get_image(image_id: str):
    conn = sqlite3.connect('images.db')
    cursor = conn.cursor()
    cursor.execute("SELECT content_type FROM images WHERE id = ?", (image_id,))
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        raise HTTPException(status_code=404, detail="Image not found")
    
    content_type = result[0]
    file_path = os.path.join(UPLOAD_DIR, image_id)
    
    try:
        return FileResponse(file_path, media_type=content_type)
    except Exception:
        # Clean up orphaned database entry if file is missing
        conn = sqlite3.connect('images.db')
        cursor = conn.cursor()
        cursor.execute("DELETE FROM images WHERE id = ?", (image_id,))
        conn.commit()
        conn.close()
        raise HTTPException(status_code=404, detail="Image not found")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)