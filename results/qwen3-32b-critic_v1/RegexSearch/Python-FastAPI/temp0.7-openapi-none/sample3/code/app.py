import os
import re
from pathlib import Path
from fastapi import FastAPI, HTTPException

app = FastAPI()

# Security and resource limits
MAX_FILE_SIZE = 1 * 1024 * 1024  # 1MB
MAX_DEPTH = 10  # Maximum directory depth for recursive search
MAX_FILES = 1000  # Maximum number of files to process per request
MAX_DIR_LENGTH = 255  # Maximum allowed directory path length
REGEX_TIMEOUT = 1  # Seconds to allow for regex search per file

# Ensure the base directory exists
os.makedirs("files", exist_ok=True)

@app.get("/search")
async def search_files(regex: str, directory: str = None, recursive: bool = False):
    # Validate regex
    try:
        pattern = re.compile(regex)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {e}")
    
    base_dir = Path("files").resolve()
    if directory is None:
        search_dir = base_dir
        if not search_dir.exists():
            raise HTTPException(status_code=400, detail="Base directory does not exist.")
        if not search_dir.is_dir():
            raise HTTPException(status_code=400, detail="Base directory is not a directory.")
    else:
        # Sanitize and validate directory path
        if len(directory) > MAX_DIR_LENGTH:
            raise HTTPException(status_code=400, detail="Directory path too long")
        
        dir_path = (base_dir / directory).resolve()
        if not dir_path.is_relative_to(base_dir):
            raise HTTPException(status_code=400, detail="Directory is outside the allowed base directory.")
        if not dir_path.exists():
            raise HTTPException(status_code=400, detail="Directory does not exist.")
        if not dir_path.is_dir():
            raise HTTPException(status_code=400, detail="Not a directory.")
        search_dir = dir_path
    
    # Collect files with depth limitation
    all_files = []
    if recursive:
        for root, dirs, files_in_root in os.walk(search_dir):
            # Calculate depth relative to search_dir
            relative_root = Path(root).relative_to(search_dir)
            depth = len(relative_root.parts)
            if depth >= MAX_DEPTH:
                dirs.clear()  # Stop recursion at max depth
            for file_name in files_in_root:
                file_path = Path(root) / file_name
                all_files.append(file_path)
    else:
        for file_name in os.listdir(search_dir):
            file_path = search_dir / file_name
            if file_path.is_file():
                all_files.append(file_path)
    
    # Limit total number of files processed
    if len(all_files) > MAX_FILES:
        raise HTTPException(status_code=400, detail="Too many files to process")
    
    matched_files = []
    for file_path in all_files:
        try:
            # Read file with size limit
            content = ''
            with open(file_path, 'r', encoding='utf-8') as f:
                while len(content) < MAX_FILE_SIZE:
                    chunk = f.read(4096)
                    if not chunk:
                        break
                    content += chunk
                if len(content) >= MAX_FILE_SIZE:
                    continue  # Skip large files
            
            # Execute regex with timeout
            if pattern.search(content, timeout=REGEX_TIMEOUT):
                relative_path = file_path.relative_to(search_dir)
                matched_files.append(str(relative_path))
        except re.error:
            # Skip files that cause regex timeout or catastrophic backtracking
            continue
        except Exception:
            # Ignore errors reading individual files
            pass
    
    return {"files": matched_files}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)