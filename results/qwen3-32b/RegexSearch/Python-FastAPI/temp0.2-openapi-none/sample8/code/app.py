import re
from fastapi import FastAPI, HTTPException
from pathlib import Path

app = FastAPI()

@app.get("/search")
async def search_files(regex: str, directory: str = None, recursive: bool = False):
    base_dir = Path(__file__).parent / "files"
    base_dir = base_dir.resolve()
    
    # Validate and construct search directory
    if directory is not None:
        search_dir = (base_dir / directory).resolve()
    else:
        search_dir = base_dir
    
    # Check if directory is valid and within base directory
    if not search_dir.exists() or not search_dir.is_dir():
        raise HTTPException(status_code=400, detail="Invalid or inaccessible directory")
    if not search_dir.is_relative_to(base_dir):
        raise HTTPException(status_code=400, detail="Directory is outside the allowed root")
    
    # Compile regex
    try:
        pattern = re.compile(regex)
    except re.error:
        raise HTTPException(status_code=400, detail="Invalid regex pattern")
    
    matched_files = []
    try:
        # Generate file list based on recursive flag
        if recursive:
            file_iter = search_dir.rglob("*")
        else:
            file_iter = search_dir.iterdir()
        
        for file_path in file_iter:
            if file_path.is_file():
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    if pattern.search(content):
                        relative_path = file_path.relative_to(search_dir).as_posix()
                        matched_files.append(relative_path)
                except Exception:
                    # Skip files that can't be read or processed
                    continue
    except PermissionError:
        raise HTTPException(status_code=400, detail="Inaccessible directory or files")
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")
    
    return {"files": matched_files}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)