import os
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

app = FastAPI()

class DownloadRequest(BaseModel):
    filename: str = Field(..., max_length=255)

@app.post("/download")
async def download_song(request: DownloadRequest):
    filename = request.filename
    base_dir = "/var/songs"
    file_path = os.path.join(base_dir, filename)
    resolved_path = os.path.abspath(file_path)
    base_dir_abs = os.path.abspath(base_dir)
    
    if not resolved_path.startswith(base_dir_abs):
        raise HTTPException(status_code=400, detail="Invalid input")
    
    if not os.path.isfile(resolved_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    return FileResponse(resolved_path, media_type="audio/mpeg")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)