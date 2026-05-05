from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel, Field
import os

app = FastAPI()

class SearchRequest(BaseModel):
    search_content: str | None = Field(default=None, max_length=1024)
    search_filename: str | None = Field(default=None, max_length=1024)
    search_dir: str | None = None

@app.post("/search")
async def search_files(request: SearchRequest):
    if request.search_content is None and request.search_filename is None:
        raise HTTPException(status_code=400, detail="At least one of search_content or search_filename must be provided")
    
    base_dir = "/data"
    if request.search_dir:
        base_dir = os.path.join("/data", request.search_dir)
        base_dir = os.path.normpath(base_dir)
        if not base_dir.startswith("/data"):
            raise HTTPException(status_code=400, detail="Invalid search_dir")
    
    results = []
    for root, _, files in os.walk(base_dir):
        for file in files:
            file_path = os.path.join(root, file)
            filename_match = request.search_filename is not None and file.startswith(request.search_filename)
            content_match = False
            if request.search_content is not None:
                try:
                    with open(file_path, 'r') as f:
                        for line in f:
                            if request.search_content in line:
                                content_match = True
                                break
                except Exception:
                    continue
            if filename_match or content_match:
                results.append(file_path)
    
    return {"files": results}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)