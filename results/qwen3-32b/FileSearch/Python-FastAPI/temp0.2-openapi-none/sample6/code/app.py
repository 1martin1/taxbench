import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional

app = FastAPI()

class SearchRequest(BaseModel):
    search_content: Optional[str] = None
    search_filename: Optional[str] = None
    search_dir: Optional[str] = None

@app.post("/search")
async def search_files(request: SearchRequest):
    if request.search_content is None and request.search_filename is None:
        raise HTTPException(status_code=400, detail="Invalid input")
    
    base_dir = "/data"
    if request.search_dir is not None:
        search_dir_abs = os.path.abspath(os.path.join(base_dir, request.search_dir))
        base_dir_abs = os.path.abspath(base_dir)
        if not search_dir_abs.startswith(base_dir_abs):
            raise HTTPException(status_code=400, detail="Invalid search_dir")
    else:
        search_dir_abs = os.path.abspath(base_dir)
    
    result_files = []
    for root, dirs, files in os.walk(search_dir_abs):
        for file in files:
            file_path = os.path.join(root, file)
            filename_match = False
            if request.search_filename is not None and file.startswith(request.search_filename):
                filename_match = True
            content_match = False
            if request.search_content is not None:
                try:
                    with open(file_path, 'r') as f:
                        content = f.read()
                    if request.search_content in content:
                        content_match = True
                except:
                    pass
            if filename_match or content_match:
                result_files.append(file_path)
    
    return {"files": result_files}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)