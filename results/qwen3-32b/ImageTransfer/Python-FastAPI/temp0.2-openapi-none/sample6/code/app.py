from fastapi import FastAPI, File, UploadFile, Path, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response, JSONResponse
import uuid
import os
import shutil

app = FastAPI()

UPLOAD_DIR = "uploads"
METADATA_DIR = "metadata"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(METADATA_DIR, exist_ok=True)

@app.post("/upload")
async def upload_image(file: UploadFile = File(...)):
    image_id = str(uuid.uuid4())
    file_path = os.path.join(UPLOAD_DIR, image_id)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    meta_path = os.path.join(METADATA_DIR, f"{image_id}.meta")
    with open(meta_path, "w") as meta_file:
        meta_file.write(file.content_type)
    return {"id": image_id}

@app.get("/images/{imageId}")
async def get_image(imageId: str = Path(..., description="The ID of the image to be viewed.")):
    file_path = os.path.join(UPLOAD_DIR, imageId)
    meta_path = os.path.join(METADATA_DIR, f"{imageId}.meta")
    if not (os.path.exists(file_path) and os.path.exists(meta_path)):
        raise HTTPException(status_code=404, detail={"error": "Image not found"})
    with open(meta_path, "r") as meta_file:
        content_type = meta_file.read().strip()
    with open(file_path, "rb") as image_file:
        image_data = image_file.read()
    return Response(content=image_data, media_type=content_type, headers={"Content-Disposition": "inline"})

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(
        status_code=400,
        content={"error": "Invalid input"},
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)