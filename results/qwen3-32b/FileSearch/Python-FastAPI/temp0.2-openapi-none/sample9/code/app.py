from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import os

app = FastAPI()

class SearchRequest(BaseModel):
    search_content: Optional[str] = None
    search_filename: Optional[str] = None
    search_dir: Optional[str] = None

@app.post("/search")
async def search_files(request: SearchRequest):
    base_dir = "/data"
    if request.search_dir is not None:
        search_path = os.path.join(base_dir, request.search_dir)
    else:
        search_path = base_dir
    
    search_path = os.path.abspath(search_path)
    base_dir_abs = os.path.abspath(base_dir)
    
    if not search_path.startswith(base_dir_abs):
        raise HTTPException(status_code=400, detail="Invalid input")
    
    if not os.path.isdir(search_path):
        return {"files": []}
    
    matched_files = []
    for root, dirs, files in os.walk(search_path):
        for filename in files:
            file_path = os.path.join(root, filename)
            filename_match = False
            if request.search_filename is not None:
                if request.search_filename in filename:
                    filename_match = True
            
            content_match = False
            if request.search_content is not None:
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        if request.search_content in content:
                            content_match = True
                except (IOError, UnicodeDecodeError):
                    pass
            
            if filename_match or content_match:
                matched_files.append(os.path.abspath(file_path))
    
    return {"files": matched_files}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)