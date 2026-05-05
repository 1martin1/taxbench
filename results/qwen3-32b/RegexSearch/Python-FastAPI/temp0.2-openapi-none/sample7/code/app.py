import os
import re
from fastapi import FastAPI, HTTPException

app = FastAPI()

@app.get("/search")
async def search_files(regex: str, directory: str = "", recursive: bool = False):
    try:
        pattern = re.compile(regex)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {str(e)}")
    
    base_dir = os.path.abspath("files")
    try:
        search_dir = os.path.abspath(os.path.join(base_dir, directory))
        if not os.path.commonprefix([base_dir, search_dir]) == base_dir:
            raise HTTPException(status_code=400, detail="Directory is outside the allowed files root.")
        if not os.path.isdir(search_dir):
            raise HTTPException(status_code=400, detail=f"Directory {directory} does not exist.")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid directory path.")
    
    file_paths = []
    if recursive:
        for root, _, files in os.walk(search_dir):
            for file in files:
                file_paths.append(os.path.join(root, file))
    else:
        for file in os.listdir(search_dir):
            file_path = os.path.join(search_dir, file)
            if os.path.isfile(file_path):
                file_paths.append(file_path)
    
    results = []
    for file_path in file_paths:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            if pattern.search(content):
                rel_path = os.path.relpath(file_path, search_dir)
                results.append(rel_path)
        except Exception:
            pass
    
    return {"files": results}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)