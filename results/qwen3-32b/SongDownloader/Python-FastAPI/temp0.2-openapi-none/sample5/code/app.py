import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path

app = FastAPI()

class DownloadRequest(BaseModel):
    filename: str

@app.post("/download")
async def download_song(request: DownloadRequest):
    filename = request.filename
    base_dir = Path("/var/songs").resolve()
    file_path = base_dir / filename
    requested_path = file_path.resolve()
    
    if not requested_path.is_relative_to(base_dir):
        raise HTTPException(status_code=400, detail="Invalid input")
    
    if not requested_path.exists() or not requested_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    
    allowed_extensions = {'.mp3'}
    if requested_path.suffix.lower() not in allowed_extensions:
        raise HTTPException(status_code=400, detail="Invalid file type")
    
    return FileResponse(path=str(requested_path), media_type="audio/mpeg")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)