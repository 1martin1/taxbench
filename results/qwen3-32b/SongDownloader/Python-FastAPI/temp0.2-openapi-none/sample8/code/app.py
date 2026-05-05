import uvicorn
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from pathlib import Path
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse

app = FastAPI()

class SongRequest(BaseModel):
    filename: str

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(
        status_code=400,
        content={"detail": "Invalid input"},
    )

@app.post("/download")
async def download_song(request: SongRequest):
    base_dir = Path("/var/songs").resolve()
    filename = request.filename
    full_path = (base_dir / filename).resolve()
    
    if not full_path.exists() or not full_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    
    try:
        full_path.relative_to(base_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    content = full_path.read_bytes()
    
    return Response(content=content, media_type="audio/mpeg")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)