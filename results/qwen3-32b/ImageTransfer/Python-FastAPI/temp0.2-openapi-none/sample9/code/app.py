from fastapi import FastAPI, UploadFile, File, HTTPException, status
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import FileResponse
import uvicorn
import uuid
import os

app = FastAPI()

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

image_metadata = {}

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return {"error": "Bad request, invalid input"}, status.HTTP_400_BAD_REQUEST

@app.exception_handler(Exception)
async def generic_exception_handler(request, exc):
    return {"error": "Internal server error"}, status.HTTP_500_INTERNAL_SERVER_ERROR

@app.post("/upload")
async def upload_image(file: UploadFile = File(...)):
    image_id = str(uuid.uuid4())
    file_location = os.path.join(UPLOAD_DIR, image_id)
    try:
        content = await file.read()
        with open(file_location, "wb") as f:
            f.write(content)
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")
    image_metadata[image_id] = file.content_type
    return {"id": image_id}

@app.get("/images/{image_id}")
async def get_image(image_id: str):
    if image_id not in image_metadata:
        raise HTTPException(status_code=404, detail="Image not found")
    file_location = os.path.join(UPLOAD_DIR, image_id)
    if not os.path.exists(file_location):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(
        path=file_location,
        media_type=image_metadata[image_id],
        filename=image_id
    )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)