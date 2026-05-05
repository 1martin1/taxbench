from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import os
from pathlib import Path

app = FastAPI()

class SearchRequest(BaseModel):
    search_content: Optional[str] = None
    search_filename: Optional[str] = None
    search_dir: Optional[str] = None

@app.post("/search")
async def search_files(request: SearchRequest):
    if request.search_content is None and request.search_filename is None:
        raise HTTPException(status_code=400, detail="At least one of search_content or search_filename must be provided")
    
    data_dir = Path("/data").resolve()
    if request.search_dir is None:
        base_dir_path = data_dir
    else:
        base_dir_path = (data_dir / request.search_dir).resolve()
    
    if not base_dir_path.is_relative_to(data_dir):
        raise HTTPException(status_code=400, detail="search_dir is outside the allowed directory")
    
    if not base_dir_path.exists():
        return {"files": []}
    
    results = []
    for root, _, files in os.walk(base_dir_path):
        for filename in files:
            file_path = os.path.join(root, filename)
            filename_match = False
            if request.search_filename is not None:
                if request.search_filename in filename:
                    filename_match = True
            content_match = False
            if request.search_content is not None:
                try:
                    with open(file_path, 'r') as f:
                        content = f.read()
                        if request.search_content in content:
                            content_match = True
                except Exception:
                    pass
            if filename_match or content_match:
                results.append(file_path)
    
    return {"files": results}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)