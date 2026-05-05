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
    base_dir = Path("/data").resolve()
    if request.search_dir is not None:
        input_dir = request.search_dir
        if Path(input_dir).is_absolute():
            raise HTTPException(status_code=400, detail="search_dir must be a relative path")
        full_search_dir = (base_dir / input_dir).resolve()
        if not full_search_dir.is_relative_to(base_dir):
            raise HTTPException(status_code=400, detail="search_dir is outside of data directory")
    else:
        full_search_dir = base_dir

    files_found = []
    try:
        for root, dirs, files in os.walk(full_search_dir):
            for file in files:
                file_path = Path(root) / file
                matches = False
                if request.search_filename is not None and file == request.search_filename:
                    matches = True
                if request.search_content is not None:
                    try:
                        with open(file_path, 'r') as f:
                            content = f.read()
                            if request.search_content in content:
                                matches = True
                    except (IOError, UnicodeDecodeError):
                        pass
                if matches:
                    files_found.append(str(file_path))
    except (FileNotFoundError, NotADirectoryError):
        pass  # Return empty list if directory doesn't exist or isn't a directory

    return {"files": files_found}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)