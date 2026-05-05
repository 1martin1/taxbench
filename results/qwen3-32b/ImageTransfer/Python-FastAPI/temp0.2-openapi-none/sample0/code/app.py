from fastapi import FastAPI, UploadFile, File, HTTPException
import uuid
import os
from fastapi.responses import FileResponse

app = FastAPI()

# In-memory storage for image metadata
image_metadata = {}

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.post("/upload")
async def upload_image(file: UploadFile = File(...)):
    image_id = str(uuid.uuid4())
    original_filename = file.filename
    file_extension = os.path.splitext(original_filename)[1].lower()
    save_path = os.path.join(UPLOAD_DIR, f"{image_id}{file_extension}")
    
    try:
        with open(save_path, "wb") as buffer:
            buffer.write(await file.read())
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e
    
    image_metadata[image_id] = {
        "content_type": file.content_type,
        "extension": file_extension
    }
    
    return {"id": image_id}

@app.get("/images/{image_id}")
async def get_image(image_id: str):
    if image_id not in image_metadata:
        raise HTTPException(status_code=404, detail="Image not found")
    
    metadata = image_metadata[image_id]
    content_type = metadata["content_type"]
    file_extension = metadata["extension"]
    filename = f"{image_id}{file_extension}"
    file_path = os.path.join(UPLOAD_DIR, filename)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Image not found")
    
    return FileResponse(
        path=file_path,
        media_type=content_type,
        headers={"Content-Disposition": "inline"}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)