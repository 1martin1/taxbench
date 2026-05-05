from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import os

app = FastAPI()

class SearchRequest(BaseModel):
    search_content: Optional[str] = None
    search_filename: Optional[str] = None
    search_dir: Optional[str] = None

def get_safe_search_path(search_dir: Optional[str]) -> str:
    base_dir = "/data"
    if search_dir is None:
        return base_dir
    full_path = os.path.normpath(os.path.join(base_dir, search_dir))
    if not os.path.commonprefix([base_dir, full_path]) == base_dir:
        raise ValueError("Invalid search_dir: cannot access outside of /data")
    return full_path

@app.post("/search")
async def search_files(request: SearchRequest):
    if request.search_content is None and request.search_filename is None:
        raise HTTPException(status_code=400, detail="At least one of search_content or search_filename must be provided")
    
    try:
        search_path = get_safe_search_path(request.search_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid search_dir: cannot access outside of /data")

    matching_files = []
    for root, dirs, files in os.walk(search_path):
        for filename in files:
            file_path = os.path.join(root, filename)
            if request.search_filename is not None and request.search_filename in filename:
                matching_files.append(file_path)
                continue
            if request.search_content is not None:
                try:
                    with open(file_path, 'r') as f:
                        content = f.read()
                    if request.search_content in content:
                        matching_files.append(file_path)
                except (IOError, UnicodeDecodeError):
                    pass
    return {"files": matching_files}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)