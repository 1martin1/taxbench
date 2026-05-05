import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List

app = FastAPI()

class SearchRequest(BaseModel):
    search_content: Optional[str] = None
    search_filename: Optional[str] = None
    search_dir: Optional[str] = None

@app.post("/search")
async def search_files(request: SearchRequest):
    if request.search_content is None and request.search_filename is None:
        raise HTTPException(status_code=400, detail="At least one of search_content or search_filename must be provided")
    
    base_dir = "/data"
    if request.search_dir is not None:
        combined = os.path.join(base_dir, request.search_dir)
        normalized = os.path.normpath(combined)
        abs_normalized = os.path.abspath(normalized)
        abs_base = os.path.abspath(base_dir)
        if not abs_normalized.startswith(abs_base + os.path.sep):
            raise HTTPException(status_code=400, detail="search_dir is outside allowed directory")
        if not os.path.isdir(normalized):
            raise HTTPException(status_code=400, detail="search_dir is not a directory")
        base_dir = normalized

    results = []
    for root, dirs, files in os.walk(base_dir):
        for filename in files:
            file_path = os.path.join(root, filename)
            filename_match = False
            if request.search_filename is not None:
                if filename.startswith(request.search_filename):
                    filename_match = True
            content_match = False
            if request.search_content is not None:
                try:
                    with open(file_path, 'r', errors='ignore') as f:
                        content = f.read()
                        if request.search_content in content:
                            content_match = True
                except Exception:
                    continue
            if filename_match or content_match:
                results.append(os.path.abspath(file_path))
    return {"files": results}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)