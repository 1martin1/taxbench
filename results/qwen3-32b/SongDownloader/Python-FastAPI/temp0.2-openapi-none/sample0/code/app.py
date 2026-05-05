import os
from fastapi import FastAPI, HTTPException, Response, Body

app = FastAPI()

@app.post("/download")
async def download_song(filename: str = Body(...)):
    base_dir = "/var/songs"
    file_path = os.path.join(base_dir, filename)
    resolved_file_path = os.path.abspath(file_path)
    base_dir_resolved = os.path.abspath(base_dir)
    
    try:
        rel_path = os.path.relpath(resolved_file_path, base_dir_resolved)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid input")
    
    if rel_path == ".." or rel_path.startswith("../"):
        raise HTTPException(status_code=400, detail="Invalid input")
    
    if not os.path.isfile(resolved_file_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    with open(resolved_file_path, "rb") as f:
        content = f.read()
    
    return Response(content=content, media_type="audio/mpeg")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)