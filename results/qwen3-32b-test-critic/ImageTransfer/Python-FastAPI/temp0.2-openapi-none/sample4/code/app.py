from fastapi import FastAPI, File, UploadFile, Path, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
import uuid
import os
import mimetypes
import tempfile

app = FastAPI()

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.post("/upload")
async def upload_image(file: UploadFile = File(...)):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Invalid file content")
    
    # Determine MIME type using file content
    with tempfile.NamedTemporaryFile(delete=False) as tmpfile:
        tmpfile.write(content)
        tmpfile_path = tmpfile.name
    mimetype, _ = mimetypes.guess_type(tmpfile_path)
    os.unlink(tmpfile_path)
    
    if not mimetype or not mimetype.startswith('image/'):
        raise HTTPException(status_code=400, detail="Invalid image format")
    
    image_id = str(uuid.uuid4())
    file_path = os.path.join(UPLOAD_DIR, image_id)
    mime_path = os.path.join(UPLOAD_DIR, f"{image_id}.mime")
    try:
        with open(file_path, "wb") as f:
            f.write(content)
        with open(mime_path, "w") as f:
            f.write(mimetype)
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")
    return {"id": image_id}

@app.get("/images/{imageId}")
async def get_image(imageId: str = Path(...)):
    mime_path = os.path.join(UPLOAD_DIR, f"{imageId}.mime")
    if not os.path.exists(mime_path):
        raise HTTPException(status_code=404, detail="Image not found")
    with open(mime_path, "r") as f:
        mime_type = f.read().strip()
    file_path = os.path.join(UPLOAD_DIR, imageId)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(
        file_path,
        media_type=mime_type,
        headers={"Content-Disposition": "inline"}
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={"error": "Bad request, invalid input"},
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)