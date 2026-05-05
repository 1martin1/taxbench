import uvicorn
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from pathlib import Path

app = FastAPI()

class DownloadRequest(BaseModel):
    filename: str

@app.post("/download")
async def download_song(request: DownloadRequest):
    base_dir = Path("/var/songs").resolve()
    filename = request.filename
    file_path = (base_dir / filename).resolve()
    
    try:
        file_path.relative_to(base_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid input")
    
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    
    content = file_path.read_bytes()
    return Response(content=content, media_type="audio/mpeg")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)