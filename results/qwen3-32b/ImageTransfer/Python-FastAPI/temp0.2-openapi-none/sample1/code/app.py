from fastapi import FastAPI, File, UploadFile, HTTPException
import os
import uuid
import mimetypes
from fastapi.responses import Response

app = FastAPI()

UPLOAD_DIR = 'uploads'

# Create upload directory if not exists
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.post("/upload")
async def upload_image(file: UploadFile = File(...)):
    try:
        # Generate unique ID
        image_id = str(uuid.uuid4())
        
        # Get original filename's extension
        original_filename = file.filename
        root, ext = os.path.splitext(original_filename)
        
        # Save the file
        file_path = os.path.join(UPLOAD_DIR, f"{image_id}{ext}")
        with open(file_path, "wb") as buffer:
            buffer.write(await file.read())
        
        return {"id": image_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/images/{imageId}")
async def get_image(imageId: str):
    try:
        upload_dir = UPLOAD_DIR
        for filename in os.listdir(upload_dir):
            if filename.startswith(imageId):
                file_path = os.path.join(upload_dir, filename)
                if os.path.isfile(file_path):
                    with open(file_path, "rb") as f:
                        content = f.read()
                    mime_type, _ = mimetypes.guess_type(filename)
                    if mime_type is None:
                        mime_type = 'application/octet-stream'
                    return Response(content=content, media_type=mime_type)
        raise HTTPException(status_code=404, detail="Image not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)