import re
import os
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query

app = FastAPI()

MAX_FILES = 1000
MAX_FILE_SIZE = 1024 * 1024  # 1MB

def is_dangerous_regex(pattern: str) -> bool:
    dangerous_patterns = [
        r'\(+[+*?]',  # Group followed by +, *, or ?
        r'[?*+]\{[0-9]+,[0-9]*\}',  # Quantifiers with {m,n}
        r'[?*+][^|&]',  # Quantifiers followed by non-alternation
    ]
    for dp in dangerous_patterns:
        if re.search(dp, pattern):
            return True
    return False

@app.get("/search")
async def search_files(
    regex: str,
    directory: str = Query(None),
    recursive: bool = Query(False)
):
    base_dir = Path("files").resolve()
    
    if directory is not None:
        search_dir = (base_dir / directory).resolve()
    else:
        search_dir = base_dir
    
    if not (search_dir.exists() and 
            search_dir.is_dir() and 
            search_dir.is_relative_to(base_dir)):
        raise HTTPException(status_code=400, detail="Invalid or inaccessible directory")
    
    try:
        if is_dangerous_regex(regex):
            raise HTTPException(status_code=400, detail="Regex contains potentially dangerous patterns")
        pattern = re.compile(regex)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {e}")
    
    file_paths = []
    if recursive:
        for root, _, files in os.walk(search_dir):
            for file in files:
                file_path = Path(root) / file
                file_paths.append(file_path)
                if len(file_paths) >= MAX_FILES:
                    break
            if len(file_paths) >= MAX_FILES:
                break
    else:
        for file in os.listdir(search_dir):
            file_path = search_dir / file
            if file_path.is_file():
                file_paths.append(file_path)
                if len(file_paths) >= MAX_FILES:
                    break
    
    results = []
    processed_files = 0
    for file_path in file_paths:
        if processed_files >= MAX_FILES:
            break
        if file_path.stat().st_size > MAX_FILE_SIZE:
            continue
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if pattern.search(line):
                        rel_path = os.path.relpath(file_path, search_dir)
                        results.append(rel_path)
                        processed_files += 1
                        break
        except Exception:
            continue
    
    return {"files": results}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)