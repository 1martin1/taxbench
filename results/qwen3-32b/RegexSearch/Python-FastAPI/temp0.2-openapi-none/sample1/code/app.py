import os
import re
from fastapi import FastAPI, HTTPException

app = FastAPI()

FILES_ROOT = "./files"

def is_safe_path(base_path, target_path):
    base_path = os.path.abspath(base_path)
    target_path = os.path.abspath(target_path)
    return os.path.commonpath([base_path, target_path]) == base_path

@app.get("/search")
async def search_files(regex: str, directory: str = None, recursive: bool = False):
    try:
        pattern = re.compile(regex)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {str(e)}")
    
    try:
        if directory is None:
            search_dir = FILES_ROOT
        else:
            search_dir = os.path.abspath(os.path.join(FILES_ROOT, directory))
        
        if not is_safe_path(FILES_ROOT, search_dir):
            raise HTTPException(status_code=400, detail="Directory is outside the allowed root.")
        
        if not os.path.isdir(search_dir):
            raise HTTPException(status_code=400, detail=f"Directory {search_dir} does not exist or is not accessible.")
        
        matched_files = []
        try:
            if recursive:
                for root, dirs, files in os.walk(search_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        try:
                            with open(file_path, 'r', errors='ignore') as f:
                                content = f.read()
                            if pattern.search(content):
                                rel_path = os.path.relpath(file_path, search_dir)
                                matched_files.append(rel_path)
                        except Exception:
                            continue
            else:
                for file in os.listdir(search_dir):
                    file_path = os.path.join(search_dir, file)
                    if os.path.isfile(file_path):
                        try:
                            with open(file_path, 'r', errors='ignore') as f:
                                content = f.read()
                            if pattern.search(content):
                                rel_path = os.path.relpath(file_path, search_dir)
                                matched_files.append(rel_path)
                        except Exception:
                            continue
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error during file search: {str(e)}")
        
        return {"files": matched_files}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)