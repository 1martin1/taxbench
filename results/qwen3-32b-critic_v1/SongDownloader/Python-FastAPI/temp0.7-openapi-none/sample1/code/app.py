from fastapi import FastAPI, HTTPException, StreamingResponse
from pydantic import BaseModel
from pathlib import Path

app = FastAPI()

class DownloadRequest(BaseModel):
    filename: str

MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB
MAX_FILENAME_LENGTH = 255

@app.post("/download")
async def download_song(request: DownloadRequest):
    base_dir = Path("/var/songs")
    
    # Verify base directory exists
    if not base_dir.exists() or not base_dir.is_dir():
        raise HTTPException(status_code=500, detail="Song directory not found")
    
    # Validate input length and basic safety
    if len(request.filename) > MAX_FILENAME_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid input")
    
    file_path = base_dir / request.filename
    
    # Verify file existence and type
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    
    # Path safety check
    resolved_path = file_path.resolve()
    base_resolved = base_dir.resolve()
    if not resolved_path.is_relative_to(base_resolved):
        raise HTTPException(status_code=400, detail="Invalid input")
    
    # File size validation
    file_size = file_path.stat().st_size
    if file_size > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large")
    
    # Stream file in chunks
    def iterfile():
        with open(resolved_path, "rb") as f:
            while chunk := f.read(1024 * 1024):  # 1MB chunks
                yield chunk
    
    return StreamingResponse(
        iterfile(),
        media_type="audio/mpeg",
        headers={"Content-Length": str(file_size)}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)