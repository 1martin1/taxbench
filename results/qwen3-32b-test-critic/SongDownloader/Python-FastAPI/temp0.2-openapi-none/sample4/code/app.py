from fastapi import FastAPI, HTTPException, Response
from fastapi.exceptions import RequestValidationError
import os

app = FastAPI()

SONG_DIR = "/var/songs"

@app.post("/download")
async def download_song(filename: str):
    if not filename.strip():
        raise HTTPException(status_code=400, detail="Invalid input")
    file_path = os.path.join(SONG_DIR, filename)
    real_path = os.path.realpath(file_path)
    if not real_path.startswith(SONG_DIR):
        raise HTTPException(status_code=400, detail="Invalid input")
    if not os.path.exists(real_path):
        raise HTTPException(status_code=404, detail="File not found")
    with open(real_path, "rb") as f:
        content = f.read()
    return Response(content=content, media_type="audio/mpeg")

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return Response(status_code=400, content="Invalid input")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)