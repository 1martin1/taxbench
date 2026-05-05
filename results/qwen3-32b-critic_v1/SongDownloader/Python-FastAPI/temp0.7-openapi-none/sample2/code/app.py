from fastapi import FastAPI, HTTPException, Response, status
from pydantic import BaseModel
from pathlib import Path

app = FastAPI()

class DownloadRequest(BaseModel):
    filename: str

@app.post("/download")
async def download_song(request: DownloadRequest):
    base_dir = Path("/var/songs").resolve()
    file_path = base_dir / request.filename
    resolved_path = file_path.resolve()
    
    if not resolved_path.startswith(base_dir):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid filename")
    
    if not resolved_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    
    # Prevent memory exhaustion by limiting file size (100MB)
    MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB
    if resolved_path.stat().st_size > MAX_FILE_SIZE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File too large")
    
    with open(resolved_path, "rb") as f:
        content = f.read()
    
    return Response(content=content, media_type="audio/mpeg")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)