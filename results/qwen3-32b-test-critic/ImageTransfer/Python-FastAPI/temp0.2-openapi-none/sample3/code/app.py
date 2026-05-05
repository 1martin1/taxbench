import os
import uuid
import mimetypes
from fastapi import FastAPI, UploadFile, File, HTTPException, status, Request
from fastapi.responses import Response, JSONResponse
from fastapi.exceptions import RequestValidationError

app = FastAPI()

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.post("/upload")
async def upload_image(file: UploadFile = File(...)):
    file_id = str(uuid.uuid4())
    original_filename = file.filename
    ext = os.path.splitext(original_filename)[1]
    file_path = os.path.join(UPLOAD_DIR, f"{file_id}{ext}")
    
    try:
        with open(file_path, "wb") as buffer:
            buffer.write(await file.read())
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    
    return {"id": file_id}

@app.get("/images/{image_id}")
async def get_image(image_id: str):
    file_path = None
    for filename in os.listdir(UPLOAD_DIR):
        if filename.startswith(image_id):
            file_path = os.path.join(UPLOAD_DIR, filename)
            break
    
    if not file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")
    
    mime_type, _ = mimetypes.guess_type(file_path)
    if mime_type is None:
        mime_type = "application/octet-stream"
    
    try:
        with open(file_path, "rb") as f:
            content = f.read()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    
    return Response(
        content=content,
        media_type=mime_type,
        headers={"Content-Disposition": "inline"}
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"error": "Bad request, invalid input"},
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)