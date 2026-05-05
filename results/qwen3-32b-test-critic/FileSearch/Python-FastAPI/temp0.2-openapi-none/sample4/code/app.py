from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from typing import Optional
import os
from pathlib import Path
import uvicorn

app = FastAPI()

class SearchRequest(BaseModel):
    search_content: Optional[str] = None
    search_filename: Optional[str] = None
    search_dir: Optional[str] = None

@app.post("/search")
async def search_files(request: SearchRequest):
    base_dir = Path("/data").resolve()
    if request.search_dir is not None:
        target_dir = (base_dir / request.search_dir).resolve()
    else:
        target_dir = base_dir

    if not target_dir.is_relative_to(base_dir) and target_dir != base_dir:
        raise HTTPException(status_code=400, detail="Invalid search directory")

    results = []
    for root, dirs, files in os.walk(target_dir):
        for filename in files:
            file_path = Path(root) / filename
            match = False

            if request.search_filename and request.search_filename in filename:
                match = True
            elif request.search_content:
                try:
                    with open(file_path, 'r') as f:
                        content = f.read()
                        if request.search_content in content:
                            match = True
                except (IOError, UnicodeDecodeError):
                    pass

            if match:
                results.append(str(file_path.resolve()))

    return {"files": results}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)