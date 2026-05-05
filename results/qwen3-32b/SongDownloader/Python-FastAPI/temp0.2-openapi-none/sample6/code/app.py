from fastapi import FastAPI, Response, HTTPException
from pydantic import BaseModel
from pathlib import Path

app = FastAPI()

class DownloadRequest(BaseModel):
    filename: str

BASE_DIR = Path("/var/songs")

@app.post("/download")
async def download_song(request: DownloadRequest):
    filename = request.filename
    file_path = BASE_DIR / filename
    resolved_path = file_path.resolve(strict=False)
    
    if not resolved_path.is_relative_to(BASE_DIR):
        raise HTTPException(status_code=400, detail="Invalid path")
    
    if not resolved_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    
    content = resolved_path.read_bytes()
    return Response(content=content, media_type="audio/mpeg")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)