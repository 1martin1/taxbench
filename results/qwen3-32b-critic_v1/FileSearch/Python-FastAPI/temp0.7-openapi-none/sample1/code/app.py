import os
import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, field_validator
from typing import Optional, List

app = FastAPI()

# Security and robustness constraints
MAX_SEARCH_DIR_LENGTH = 255
MAX_DEPTH = 5
MAX_FILES = 1000
MAX_FILE_SIZE = 1024 * 1024  # 1MB
SEARCH_TIMEOUT = 30  # seconds

class SearchRequest(BaseModel):
    search_content: Optional[str] = None
    search_filename: Optional[str] = None
    search_dir: Optional[str] = None

    @field_validator('search_dir', mode='before')
    def validate_search_dir_length(cls, v):
        if v is not None and len(v) > MAX_SEARCH_DIR_LENGTH:
            raise ValueError("search_dir exceeds maximum length")
        return v

def run_search(search_path: str, request: SearchRequest) -> List[str]:
    matching_files = []
    for root, dirs, files in os.walk(search_path):
        # Calculate and enforce max depth
        current_depth = len(root.split(os.sep)) - len(search_path.split(os.sep))
        if current_depth > MAX_DEPTH:
            dirs.clear()
        
        for filename in files:
            file_path = os.path.join(root, filename)
            
            # Exact filename match check
            if request.search_filename is not None and filename == request.search_filename:
                if len(matching_files) < MAX_FILES:
                    matching_files.append(file_path)
            
            # Content match check with size limit and chunked reading
            elif request.search_content is not None:
                try:
                    if os.path.getsize(file_path) > MAX_FILE_SIZE:
                        continue
                except OSError:
                    continue
                
                try:
                    with open(file_path, 'rb') as f:
                        chunk_size = 8192
                        content_bytes = request.search_content.encode('utf-8')
                        while chunk := f.read(chunk_size):
                            if content_bytes in chunk:
                                if len(matching_files) < MAX_FILES:
                                    matching_files.append(file_path)
                                break
                except Exception:
                    pass
    
    return matching_files

@app.post("/search")
async def search_files(request: SearchRequest):
    if request.search_content is None and request.search_filename is None:
        raise HTTPException(status_code=400, detail="At least one of search_content or search_filename must be provided")

    base_dir = "/data"
    search_dir = request.search_dir or ""
    search_path = os.path.join(base_dir, search_dir)
    real_search_path = os.path.realpath(search_path)
    real_base_dir = os.path.realpath(base_dir)

    if not real_search_path.startswith(real_base_dir):
        raise HTTPException(status_code=400, detail="Invalid search directory")

    if not os.path.isdir(real_search_path):
        return {"files": []}

    try:
        # Run search in separate thread with timeout
        result = await asyncio.wait_for(
            asyncio.to_thread(run_search, real_search_path, request),
            timeout=SEARCH_TIMEOUT
        )
        return {"files": result}
    except asyncio.TimeoutError:
        raise HTTPException(status_code=400, detail="Search operation timed out")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)