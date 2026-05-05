import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
from pathlib import Path

app = FastAPI()

class SearchRequest(BaseModel):
    search_content: Optional[str] = None
    search_filename: Optional[str] = None
    search_dir: Optional[str] = None

@app.post("/search")
async def search_files(request: SearchRequest):
    base_dir = Path("/data").resolve()
    
    if request.search_dir is not None:
        candidate = (base_dir / request.search_dir).resolve()
        if not candidate.is_relative_to(base_dir):
            raise HTTPException(status_code=400, detail="Invalid search_dir")
        search_path = candidate
    else:
        search_path = base_dir

    results = []
    for root, dirs, files in os.walk(search_path):
        for filename in files:
            file_path = Path(root) / filename
            filename_match = False
            if request.search_filename is not None:
                if filename.startswith(request.search_filename):
                    filename_match = True
            content_match = False
            if request.search_content is not None:
                try:
                    with open(file_path, 'r') as f:
                        content = f.read()
                        if request.search_content in content:
                            content_match = True
                except (IOError, UnicodeDecodeError):
                    pass
            if filename_match or content_match:
                results.append(str(file_path.resolve()))
    
    return {"files": results}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)