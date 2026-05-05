from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from pathlib import Path

app = FastAPI()

class DownloadRequest(BaseModel):
    filename: str

@app.post("/download")
async def download_song(request: DownloadRequest):
    base_dir = Path("/var/songs")
    file_path = base_dir / request.filename
    
    try:
        resolved_path = file_path.resolve(strict=False)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    if not resolved_path.is_relative_to(base_dir):
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    if not resolved_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    if not resolved_path.is_file():
        raise HTTPException(status_code=400, detail="Invalid input")
    
    content = resolved_path.read_bytes()
    return Response(content=content, media_type="audio/mpeg")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)