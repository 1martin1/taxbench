import os
from fastapi import FastAPI, HTTPException, Request, StreamingResponse

app = FastAPI()

SONG_DIR = "/var/songs"
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB

def file_iterator(file_path: str, chunk_size: int = 64 * 1024):
    with open(file_path, mode="rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            yield chunk

@app.post("/download")
async def download_song(request: Request):
    try:
        data = await request.json()
    except:
        raise HTTPException(status_code=400, detail="Invalid input")
    
    filename = data.get("filename")
    if not isinstance(filename, str) or not filename.strip():
        raise HTTPException(status_code=400, detail="Invalid input")
    
    try:
        file_path = os.path.join(SONG_DIR, filename)
        abs_path = os.path.abspath(file_path)
        song_dir_abs = os.path.abspath(SONG_DIR)
        
        if not abs_path.startswith(song_dir_abs):
            raise HTTPException(status_code=400, detail="Invalid input")
            
        if not os.path.isfile(abs_path):
            raise HTTPException(status_code=404, detail="File does not exist")
            
        file_size = os.path.getsize(abs_path)
        if file_size > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail="File too large")
            
        return StreamingResponse(
            content=file_iterator(abs_path),
            media_type="audio/mpeg"
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)