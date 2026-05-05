from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel
from pathlib import Path
import os

class SearchRequest(BaseModel):
    search_content: str | None = None
    search_filename: str | None = None
    search_dir: str | None = None

app = FastAPI()

@app.post("/search")
async def search_files(request: SearchRequest):
    base_dir = Path("/data")
    current_dir = base_dir
    if request.search_dir is not None:
        target_dir = base_dir / request.search_dir
        try:
            resolved_dir = target_dir.resolve(strict=False)
        except (ValueError, OSError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid search_dir")
        if not resolved_dir.is_relative_to(base_dir):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="search_dir is outside allowed directory")
        if resolved_dir.is_symlink():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="search_dir is a symbolic link")
        if not resolved_dir.exists() or not resolved_dir.is_dir():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="search_dir is invalid")
        current_dir = resolved_dir

    results = []
    for root, dirs, files in os.walk(current_dir):
        # Remove symlinked directories to prevent following them
        for dir_name in list(dirs):
            dir_path = Path(root) / dir_name
            if dir_path.is_symlink():
                dirs.remove(dir_name)
        for filename in files:
            file_path = Path(root) / filename
            # Check filename condition
            filename_match = False
            if request.search_filename is None:
                filename_match = True
            else:
                if filename.startswith(request.search_filename):
                    filename_match = True
            # Check content condition
            content_match = False
            if request.search_content is None:
                content_match = True
            else:
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    if request.search_content in content:
                        content_match = True
                except Exception:
                    content_match = False
            if filename_match or content_match:
                results.append(str(file_path))
    return {"files": results}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)