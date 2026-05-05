import os
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from pathlib import Path

app = FastAPI()

class FileRequest(BaseModel):
    filename: str

@app.post("/download")
async def download_song(file_request: FileRequest):
    if not file_request.filename.strip():
        raise HTTPException(status_code=400, detail="Invalid input")
    base_dir = Path("/var/songs").resolve()
    file_path = (base_dir / file_request.filename).resolve()
    if not file_path.is_relative_to(base_dir):
        raise HTTPException(status_code=400, detail="Invalid input")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if not file_path.is_file():
        raise HTTPException(status_code=400, detail="Invalid input")
    with open(file_path, "rb") as f:
        content = f.read()
    return Response(content=content, media_type="audio/mpeg")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)