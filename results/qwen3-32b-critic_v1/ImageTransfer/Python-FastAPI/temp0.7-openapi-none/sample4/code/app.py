from fastapi import FastAPI, File, UploadFile, HTTPException, JSONResponse
from fastapi.exceptions import RequestValidationError
import uuid
import os
from fastapi.responses import FileResponse

app = FastAPI()

# In-memory storage for content types
image_info = {}

# Create uploads directory if not exists
os.makedirs("uploads", exist_ok=True)

def get_mime_type(signature: bytes) -> str:
    if signature.startswith(b'\xFF\xD8\xFF'):
        return 'image/jpeg'
    elif signature.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    elif signature.startswith(b'GIF87a') or signature.startswith(b'GIF89a'):
        return 'image/gif'
    elif signature.startswith(b'BM'):
        return 'image/bmp'
    elif signature.startswith(b'RIFF') and signature[8:12] == b'WEBP':
        return 'image/webp'
    else:
        return 'application/octet-stream'

@app.post("/upload")
async def upload_image(file: UploadFile = File(..., max_length=10*1024*1024)):  # 10MB limit
    unique_id = uuid.uuid4().hex
    file_path = f"uploads/{unique_id}"
    try:
        # Read first 20 bytes for MIME detection
        first_chunk = await file.read(20)
        mime_type = get_mime_type(first_chunk)
        
        # Write the first chunk and the rest
        with open(file_path, "wb") as buffer:
            buffer.write(first_chunk)
            while True:
                chunk = await file.read(1024 * 1024)  # Read 1MB chunks
                if not chunk:
                    break
                buffer.write(chunk)
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")
    image_info[unique_id] = mime_type
    return {"id": unique_id}

@app.get("/images/{image_id}")
async def get_image(image_id: str):
    content_type = image_info.get(image_id)
    if not content_type:
        raise HTTPException(status_code=404, detail="Image not found")
    file_path = f"uploads/{image_id}"
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(
        file_path, 
        media_type=content_type,
        headers={"Content-Disposition": "inline"}
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(
        status_code=400,
        content={"error": "Invalid input data"},
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)