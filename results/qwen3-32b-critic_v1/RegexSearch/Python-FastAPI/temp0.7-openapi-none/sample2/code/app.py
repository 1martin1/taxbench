from fastapi import FastAPI, HTTPException, Query
import re
import os
import threading
from pathlib import Path

app = FastAPI()

FILES_ROOT = Path(__file__).parent / "files"

# Security/robustness constants
MAX_FILES = 1000
MAX_FILE_SIZE = 1024 * 1024  # 1MB
MAX_REGEX_LENGTH = 100
REGEX_TIMEOUT = 1  # seconds

@app.get("/search")
def search_files(
    regex: str,
    directory: str = Query(None),
    recursive: bool = Query(False)
):
    # Validate regex length
    if len(regex) > MAX_REGEX_LENGTH:
        raise HTTPException(status_code=400, detail="Regex is too long")
    
    # Validate regex with timeout
    result = [False]
    def compile_regex():
        try:
            re.compile(regex)
            result[0] = True
        except re.error:
            result[0] = False
    thread = threading.Thread(target=compile_regex)
    thread.start()
    thread.join(timeout=REGEX_TIMEOUT)
    if thread.is_alive():
        raise HTTPException(status_code=400, detail="Regex compilation timeout")
    if not result[0]:
        raise HTTPException(status_code=400, detail="Invalid regex")
    
    # Determine target directory
    if directory is not None:
        target_dir = FILES_ROOT / directory
    else:
        target_dir = FILES_ROOT
    
    # Check for directory traversal
    try:
        resolved_target = target_dir.resolve()
        resolved_root = FILES_ROOT.resolve()
        if not resolved_target.is_relative_to(resolved_root):
            raise HTTPException(status_code=400, detail="Directory is outside the allowed root")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid directory path")
    
    # Check if directory exists and is accessible
    if not target_dir.exists():
        raise HTTPException(status_code=400, detail="Directory does not exist")
    if not target_dir.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory")
    if not os.access(str(target_dir), os.R_OK):
        raise HTTPException(status_code=400, detail="Directory is not accessible")
    
    # Collect files with size limit
    files_to_search = []
    if recursive:
        for root, dirs, files in os.walk(str(target_dir)):
            for file in files:
                file_path = Path(root) / file
                files_to_search.append(file_path)
                if len(files_to_search) > MAX_FILES:
                    raise HTTPException(status_code=400, detail="Too many files to process")
    else:
        for file in target_dir.iterdir():
            if file.is_file():
                files_to_search.append(file)
                if len(files_to_search) > MAX_FILES:
                    raise HTTPException(status_code=400, detail="Too many files to process")
    
    # Process each file with size and timeout checks
    matching_files = []
    for file_path in files_to_search:
        try:
            if os.path.getsize(file_path) > MAX_FILE_SIZE:
                continue  # Skip large files
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            # Run regex search with timeout
            search_result = [False]
            def run_search():
                try:
                    pattern = re.compile(regex)
                    search_result[0] = pattern.search(content) is not None
                except re.error:
                    search_result[0] = False
            thread = threading.Thread(target=run_search)
            thread.start()
            thread.join(timeout=REGEX_TIMEOUT)
            if thread.is_alive():
                raise HTTPException(status_code=500, detail="Regex processing timeout")
            if search_result[0]:
                relative_path = file_path.relative_to(target_dir)
                matching_files.append(str(relative_path))
        except Exception:
            pass  # Per original code, silently ignore errors
    
    return {"files": matching_files}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)