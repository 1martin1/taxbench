import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, model_validator
from typing import Optional

app = FastAPI()

MAX_DEPTH = 5
MAX_FILE_SIZE = 1 * 1024 * 1024  # 1MB
MAX_RESULTS = 100

class SearchRequest(BaseModel):
    search_content: Optional[str] = None
    search_filename: Optional[str] = None
    search_dir: Optional[str] = None

    @model_validator(mode='after')
    def check_search_criteria(self):
        if self.search_content is None and self.search_filename is None:
            raise ValueError("At least one of search_content or search_filename must be provided")
        return self

@app.post("/search")
async def search_files(request: SearchRequest):
    base_dir = "/data"
    if request.search_dir is not None:
        base_dir_candidate = os.path.abspath(os.path.join("/data", request.search_dir))
        if not base_dir_candidate.startswith("/data"):
            raise HTTPException(status_code=400, detail="search_dir is outside the allowed directory")
        if not os.path.isdir(base_dir_candidate):
            raise HTTPException(status_code=400, detail="search_dir does not exist")
        base_dir = base_dir_candidate
    
    if not os.path.isdir(base_dir):
        raise HTTPException(status_code=400, detail="search_dir does not exist")

    result_files = []
    
    def handle_walk_error(error):
        print(f"Directory traversal error: {error}")

    try:
        for root, dirs, files in os.walk(base_dir, onerror=handle_walk_error):
            # Limit directory traversal depth
            current_depth = len(root.split(os.path.sep)) - len(base_dir.split(os.path.sep))
            if current_depth >= MAX_DEPTH:
                dirs.clear()  # Prevent deeper traversal
            
            for file in files:
                file_path = os.path.join(root, file)
                filename_match = False
                if request.search_filename is not None:
                    if request.search_filename in file:
                        filename_match = True
                
                content_match = False
                if request.search_content is not None:
                    try:
                        # Skip large files to prevent memory exhaustion
                        if os.path.getsize(file_path) > MAX_FILE_SIZE:
                            continue
                        with open(file_path, 'r') as f:
                            content = f.read()
                        if request.search_content in content:
                            content_match = True
                    except (IOError, UnicodeDecodeError, PermissionError) as e:
                        print(f"Error reading file {file_path}: {e}")
                        continue
                
                if filename_match or content_match:
                    result_files.append(file_path)
                    if len(result_files) >= MAX_RESULTS:
                        break  # Stop processing when max results reached
            if len(result_files) >= MAX_RESULTS:
                break  # Exit outer loop when max results reached
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e

    return {"files": result_files}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)