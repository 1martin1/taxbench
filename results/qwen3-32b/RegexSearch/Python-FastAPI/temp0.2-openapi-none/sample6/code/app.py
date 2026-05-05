import os
import re
import uvicorn
from fastapi import FastAPI, HTTPException, Query

app = FastAPI()

@app.get("/search")
async def search_files(
    regex: str,
    directory: str = Query(None),
    recursive: bool = Query(False)
):
    # Determine the absolute path of the search directory
    if directory is not None:
        temp_base = os.path.join("files", directory)
    else:
        temp_base = "files"
    base_dir_abs = os.path.abspath(temp_base)
    files_root_abs = os.path.abspath("files")
    
    # Ensure the directory is within the allowed files root
    if not base_dir_abs.startswith(files_root_abs):
        raise HTTPException(status_code=400, detail="Directory is outside the allowed files root")
    
    # Check if the directory exists and is accessible
    if not os.path.exists(base_dir_abs) or not os.path.isdir(base_dir_abs):
        raise HTTPException(status_code=400, detail=f"Directory '{directory}' does not exist or is not accessible")
    
    # Compile the regex pattern
    try:
        pattern = re.compile(regex)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {str(e)}")
    
    # Collect files to search
    files_to_search = []
    if recursive:
        for root, dirs, files in os.walk(base_dir_abs):
            for file in files:
                files_to_search.append(os.path.join(root, file))
    else:
        for file_name in os.listdir(base_dir_abs):
            file_path = os.path.join(base_dir_abs, file_name)
            if os.path.isfile(file_path):
                files_to_search.append(file_path)
    
    # Search files for regex matches
    results = []
    for file_path in files_to_search:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            if pattern.search(content):
                relative_path = os.path.relpath(file_path, base_dir_abs)
                results.append(relative_path)
        except Exception:
            # Skip files that cannot be read or processed
            pass
    
    return {"files": results}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)