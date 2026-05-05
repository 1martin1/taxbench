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
    search_content = request.search_content
    search_filename = request.search_filename
    search_dir = request.search_dir

    if search_content is None and search_filename is None:
        raise HTTPException(status_code=400, detail="Invalid input")

    base_dir = "/data"
    if search_dir:
        full_dir = os.path.normpath(os.path.join(base_dir, search_dir))
        base_real = os.path.realpath(base_dir)
        full_real = os.path.realpath(full_dir)
        if not full_real.startswith(base_real):
            raise HTTPException(status_code=400, detail="Invalid search_dir")
    else:
        full_real = os.path.realpath(base_dir)

    if not os.path.isdir(full_real):
        return {"files": []}

    result_files = []
    for root, dirs, files in os.walk(full_real):
        for filename in files:
            file_path = os.path.join(root, filename)
            filename_match = False
            if search_filename is not None and search_filename in filename:
                filename_match = True
            content_match = False
            if search_content is not None:
                try:
                    with open(file_path, 'r') as f:
                        content = f.read()
                    if search_content in content:
                        content_match = True
                except Exception:
                    pass
            if filename_match or content_match:
                result_files.append(file_path)

    return {"files": result_files}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)