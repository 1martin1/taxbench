from fastapi import FastAPI, HTTPException, Response, status
from pydantic import BaseModel
from pathlib import Path

app = FastAPI()

class DownloadRequest(BaseModel):
    filename: str

@app.post("/download")
async def download_song(request: DownloadRequest):
    base_dir = Path("/var/songs").resolve()
    file_path = (base_dir / request.filename).resolve()
    
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    
    if not file_path.is_relative_to(base_dir):
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    content = file_path.read_bytes()
    return Response(content=content, media_type="audio/mpeg")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)