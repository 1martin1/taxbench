import os
import re
from fastapi import FastAPI, HTTPException
from concurrent.futures import ThreadPoolExecutor, TimeoutError

app = FastAPI()
BASE_DIR = "./files"

# Security constraints
MAX_FILE_SIZE = 1024 * 1024  # 1 MB
MAX_FILES_PROCESSED = 1000
REGEX_SEARCH_TIMEOUT = 1  # seconds

def match_with_timeout(pattern, content, timeout=REGEX_SEARCH_TIMEOUT):
    """Run regex search with timeout using thread pool executor"""
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(pattern.search, content)
        try:
            result = future.result(timeout=timeout)
            return result is not None
        except TimeoutError:
            return False

@app.get("/search")
def search_files(regex: str, directory: str = "", recursive: bool = False):
    # Validate and normalize directory path
    search_dir = os.path.normpath(os.path.join(BASE_DIR, directory))
    
    # Check if directory exists and is a directory
    if not os.path.exists(search_dir):
        raise HTTPException(status_code=400, detail="Directory does not exist")
    if not os.path.isdir(search_dir):
        raise HTTPException(status_code=400, detail="Not a directory")
    
    # Prevent directory traversal attacks
    resolved_search_dir = os.path.realpath(search_dir)
    resolved_base_dir = os.path.realpath(BASE_DIR)
    if os.path.commonpath([resolved_base_dir, resolved_search_dir]) != resolved_base_dir:
        raise HTTPException(status_code=400, detail="Directory traversal not allowed")
    
    # Validate regex pattern
    try:
        pattern = re.compile(regex)
    except re.error as e:
        raise HTTPException(status_code=400, detail="Invalid regex pattern")
    
    results = []
    file_count = 0
    
    try:
        if recursive:
            visited = set()
            for root, dirs, files in os.walk(search_dir):
                # Prevent infinite loops with symbolic links
                if root in visited:
                    dirs.clear()
                    continue
                visited.add(root)
                
                for file in files:
                    file_path = os.path.join(root, file)
                    try:
                        # Read with file size limit
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read(MAX_FILE_SIZE + 1)
                        
                        if len(content) > MAX_FILE_SIZE:
                            continue  # Skip large files
                        
                        # Search with timeout
                        if match_with_timeout(pattern, content):
                            rel_path = os.path.relpath(file_path, search_dir)
                            results.append(rel_path)
                            file_count += 1
                            
                            if file_count >= MAX_FILES_PROCESSED:
                                raise HTTPException(status_code=500, detail="Too many files processed")
                    except (IOError, UnicodeDecodeError, OSError):
                        pass
        else:
            for file in os.listdir(search_dir):
                file_path = os.path.join(search_dir, file)
                if os.path.isfile(file_path):
                    try:
                        # Read with file size limit
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read(MAX_FILE_SIZE + 1)
                        
                        if len(content) > MAX_FILE_SIZE:
                            continue  # Skip large files
                        
                        # Search with timeout
                        if match_with_timeout(pattern, content):
                            results.append(file)
                            file_count += 1
                            
                            if file_count >= MAX_FILES_PROCESSED:
                                raise HTTPException(status_code=500, detail="Too many files processed")
                    except (IOError, UnicodeDecodeError, OSError):
                        pass
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")
    
    return {"files": results}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)