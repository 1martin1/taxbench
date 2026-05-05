from fastapi import FastAPI, HTTPException
import os
import re
from pathlib import Path

app = FastAPI()

BASE_DIR = Path("files").resolve()

@app.get("/search")
async def search_files(regex: str, directory: str = "", recursive: bool = False):
    try:
        # Validate and construct the search directory
        search_dir = BASE_DIR / directory
        search_dir = search_dir.resolve()
        if not search_dir.is_relative_to(BASE_DIR):
            raise HTTPException(status_code=400, detail="Directory is outside the allowed root.")
        if not search_dir.exists():
            raise HTTPException(status_code=400, detail="Directory does not exist.")
        if not search_dir.is_dir():
            raise HTTPException(status_code=400, detail="Path is not a directory.")
        
        # Compile regex
        pattern = re.compile(regex)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    # Collect files
    files_to_check = []
    try:
        if recursive:
            for root, dirs, files in os.walk(search_dir):
                for file in files:
                    files_to_check.append(Path(root) / file)
        else:
            for file in os.listdir(search_dir):
                file_path = search_dir / file
                if file_path.is_file():
                    files_to_check.append(file_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error accessing directory: {e}")
    
    # Check each file
    matched_files = []
    for file_path in files_to_check:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            if pattern.search(content):
                rel_path = file_path.relative_to(search_dir)
                matched_files.append(str(rel_path))
        except (UnicodeDecodeError, OSError):
            # Skip files that can't be read or are not text
            continue
    
    return {"files": matched_files}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)