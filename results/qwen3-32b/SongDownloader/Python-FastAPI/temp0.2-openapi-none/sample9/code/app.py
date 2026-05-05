from fastapi import FastAPI, HTTPException, status, exceptions
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from pathlib import Path

app = FastAPI()

class DownloadRequest(BaseModel):
    filename: str

@app.exception_handler(exceptions.RequestValidationError)
@app.exception_handler(exceptions.ValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "Invalid input"},
    )

@app.post("/download")
async def download_song(request: DownloadRequest):
    base_dir = Path("/var/songs").resolve()
    filename = request.filename.strip()
    if not filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid input")
    file_path = base_dir / filename
    try:
        resolved_path = file_path.resolve()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid path")
    if not resolved_path.is_relative_to(base_dir):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid input")
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return FileResponse(path=file_path, media_type="audio/mpeg")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)