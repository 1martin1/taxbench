from fastapi import FastAPI, Response, RequestValidationError
from pydantic import BaseModel, field_validator
import os

app = FastAPI()

class DownloadRequest(BaseModel):
    filename: str

    @field_validator('filename')
    def validate_filename(cls, value: str) -> str:
        if len(value) > 255:
            raise ValueError("Filename too long")
        ext = os.path.splitext(value)[1][1:].lower()
        if ext not in {'mp3'}:
            raise ValueError("Invalid file extension")
        return value

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return Response("Invalid input", status_code=400)

@app.post("/download")
async def download_song(request: DownloadRequest):
    base_dir = "/var/songs"
    filename = request.filename
    file_path = os.path.join(base_dir, filename)
    normalized_path = os.path.normpath(file_path)
    
    if not normalized_path.startswith(base_dir):
        return Response("Invalid filename", status_code=400)
    
    if not os.path.exists(normalized_path):
        return Response("File not found", status_code=404)
    
    if not os.path.isfile(normalized_path):
        return Response("File not found", status_code=404)
    
    try:
        with open(normalized_path, "rb") as f:
            content = f.read()
    except Exception:
        return Response("File not found", status_code=404)
    
    return Response(content=content, media_type="audio/mpeg")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)