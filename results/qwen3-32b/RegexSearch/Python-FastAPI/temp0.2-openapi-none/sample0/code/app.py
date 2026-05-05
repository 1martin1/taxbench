import os
import re
from fastapi import FastAPI, HTTPException
from pathlib import Path

app = FastAPI()

BASE_DIR = Path("./files").resolve()

@app.get("/search")
async def search_files(regex: str, directory: str = None, recursive: bool = False):
    try:
        pattern = re.compile(regex)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {e}")

    try:
        if directory is None:
            full_dir = BASE_DIR
        else:
            full_dir = (BASE_DIR / directory).resolve()
        
        if not full_dir.is_relative_to(BASE_DIR):
            raise HTTPException(status_code=400, detail="Directory traversal attempt detected")
            
        if not full_dir.exists() or not full_dir.is_dir():
            raise HTTPException(status_code=400, detail="Directory does not exist or is not accessible")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Directory error: {e}")

    matched_files = []
    for root, dirs, files in os.walk(full_dir):
        if not recursive:
            dirs.clear()
        for file in files:
            file_path = Path(root) / file
            try:
                with file_path.open("r", encoding="utf-8") as f:
                    content = f.read()
                if pattern.search(content):
                    rel_path = file_path.relative_to(full_dir)
                    matched_files.append(str(rel_path))
            except Exception:
                pass

    return {"files": matched_files}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)