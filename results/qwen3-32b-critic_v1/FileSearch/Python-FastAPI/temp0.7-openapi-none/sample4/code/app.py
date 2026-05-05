import os
import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from pathlib import Path

app = FastAPI()

class SearchRequest(BaseModel):
    search_content: Optional[str] = Field(default=None, max_length=1024)
    search_filename: Optional[str] = Field(default=None, max_length=1024)
    search_dir: Optional[str] = Field(default=None, max_length=1024)

@app.post("/search")
async def search_files(request: SearchRequest):
    # Require at least one search criterion
    if request.search_content is None and request.search_filename is None:
        raise HTTPException(status_code=400, detail="At least one of search_content or search_filename must be provided")

    base_dir = Path("/data").resolve()
    
    if request.search_dir is not None:
        search_path = (base_dir / request.search_dir).resolve()
    else:
        search_path = base_dir

    if not search_path.is_relative_to(base_dir):
        raise HTTPException(status_code=400, detail="Invalid search_dir: cannot escape /data directory")

    MAX_DEPTH = 5
    MAX_FILE_SIZE = 1024 * 1024  # 1 MB
    MAX_FILES_TO_PROCESS = 1000

    def process_files():
        result_files = []
        for root, dirs, files in os.walk(str(search_path)):
            current_depth = len(Path(root).parts) - len(base_dir.parts)
            if current_depth >= MAX_DEPTH:
                dirs.clear()  # Stop descending further
            for file_name in files:
                file_path = Path(root) / file_name
                try:
                    if file_path.stat().st_size > MAX_FILE_SIZE:
                        continue
                except Exception:
                    continue

                if len(result_files) >= MAX_FILES_TO_PROCESS:
                    break

                filename_matches = False
                if request.search_filename is not None and request.search_filename in file_name:
                    filename_matches = True

                content_in_file = False
                if request.search_content is not None:
                    try:
                        with open(file_path, 'r', errors='ignore') as f:
                            content = f.read()
                        content_in_file = request.search_content in content
                    except Exception:
                        content_in_file = False

                if filename_matches or content_in_file:
                    result_files.append(str(file_path))

                if len(result_files) >= MAX_FILES_TO_PROCESS:
                    break
            if len(result_files) >= MAX_FILES_TO_PROCESS:
                break
        return result_files

    try:
        async with asyncio.timeout(30):  # 30 second timeout
            files = await asyncio.to_thread(process_files)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="Request timeout")

    return {"files": files}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)