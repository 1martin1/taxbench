from fastapi import FastAPI, File, UploadFile, HTTPException, status, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
import uuid
import os
from pathlib import Path
import mimetypes

app = FastAPI()

@app.on_event("startup")
def create_upload_dir():
    os.makedirs("uploads", exist_ok=True)

@app.post("/upload")
async def upload_image(file: UploadFile = File(...)):
    image_id = uuid.uuid4().hex
    original_filename = file.filename
    suffix = Path(original_filename).suffix
    save_path = Path("uploads") / f"{image_id}{suffix}"
    try:
        content = await file.read()
        with open(save_path, "wb") as f:
            f.write(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")
    return {"id": image_id}

@app.get("/images/{image_id}")
async def get_image(image_id: str):
    upload_dir = "uploads"
    found_file = None
    mime_type = "application/octet-stream"
    for filename in os.listdir(upload_dir):
        if filename.startswith(image_id):
            found_file = os.path.join(upload_dir, filename)
            mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            break
    if not found_file:
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(found_file, media_type=mime_type)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={"error": "Bad request, invalid input"},
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)