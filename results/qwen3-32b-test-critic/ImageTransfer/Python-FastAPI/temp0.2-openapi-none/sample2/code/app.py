import os
import uuid
import json
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import FileResponse
import uvicorn

app = FastAPI()

# Ensure the upload directory exists
os.makedirs("uploads", exist_ok=True)

ALLOWED_CONTENT_TYPES = {
    'image/jpeg',
    'image/png',
    'image/gif',
    'image/webp',
    'image/tiff',
    'image/bmp',
    'image/x-icon',
    'image/heic',
    'image/avif',
}

@app.post("/upload")
async def upload_image(file: UploadFile = File(...)):
    try:
        content_type = file.content_type
        if not content_type or content_type not in ALLOWED_CONTENT_TYPES:
            raise HTTPException(status_code=400, detail="Invalid file type")
        
        image_id = str(uuid.uuid4())
        file_location = f"uploads/{image_id}"
        meta_location = f"uploads/{image_id}.meta"
        
        # Save the image file
        with open(file_location, "wb") as buffer:
            buffer.write(await file.read())
        
        # Save metadata
        meta_data = {"content_type": content_type}
        with open(meta_location, "w") as meta_file:
            json.dump(meta_data, meta_file)
            
        return {"id": image_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e

@app.get("/images/{image_id}")
async def get_image(image_id: str):
    image_path = f"uploads/{image_id}"
    meta_path = f"uploads/{image_id}.meta"
    
    if not (os.path.exists(image_path) and os.path.exists(meta_path)):
        raise HTTPException(status_code=404, detail="Image not found")
    
    try:
        with open(meta_path, "r") as f:
            meta = json.load(f)
        content_type = meta.get("content_type", "application/octet-stream")
        return FileResponse(image_path, media_type=content_type, headers={"Content-Disposition": "inline"})
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)