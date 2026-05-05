from fastapi import FastAPI, HTTPException, Query
import re
import os
from pathlib import Path
import asyncio

app = FastAPI()

def is_dangerous_regex(pattern: str) -> bool:
    """Check for potentially dangerous regex patterns that could cause ReDoS."""
    dangerous_patterns = [
        r'\(\.\*\)',  # (.*)
        r'\(\.\+\)',  # (.+)
        r'\(\.\?\)',  # (?.)
        r'(?:\*|\+|\?){2}',  # consecutive quantifiers like a++ or (a+)+
        r'(?:\{[0-9,]+\}){2}',  # nested quantifiers with {}
    ]
    for dp in dangerous_patterns:
        try:
            if re.search(dp, pattern):
                return True
        except re.error:
            # Invalid regex in the pattern, skip
            pass
    return False

MAX_FILE_COUNT = 1000
MAX_FILE_SIZE = 1024 * 1024  # 1 MB
REGEX_COMPILE_TIMEOUT = 5  # seconds

@app.get("/search")
async def search_files(
    regex: str,
    directory: str = Query(None),
    recursive: bool = Query(False)
):
    # Validate regex for potential ReDoS patterns
    if is_dangerous_regex(regex):
        raise HTTPException(status_code=400, detail="Regex pattern is potentially dangerous and may cause performance issues.")
    
    # Compile regex with timeout
    try:
        pattern = re.compile(regex, timeout=REGEX_COMPILE_TIMEOUT)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Regex compilation error: {e}")

    # Determine search directory
    base_dir = Path("./files").resolve()
    try:
        if directory is not None:
            search_path = (base_dir / directory).resolve()
        else:
            search_path = base_dir

        # Check if search path is within base directory
        if not search_path.is_relative_to(base_dir):
            raise HTTPException(status_code=400, detail="Directory is outside allowed path")

        # Check if directory exists and is a directory
        if not search_path.exists():
            raise HTTPException(status_code=400, detail=f"Directory does not exist: {search_path}")
        if not search_path.is_dir():
            raise HTTPException(status_code=400, detail=f"Path is not a directory: {search_path}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Traverse directory with file count limit
    files_to_search = []
    try:
        for root, dirs, files in os.walk(search_path):
            for file in files:
                file_path = Path(root) / file
                files_to_search.append(file_path)
                if len(files_to_search) >= MAX_FILE_COUNT:
                    raise HTTPException(status_code=400, detail=f"Too many files in directory. Limit exceeded ({MAX_FILE_COUNT}).")
            if not recursive:
                dirs.clear()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error traversing directory: {e}")

    # Search files with size limit
    results = []
    for file_path in files_to_search:
        # Compute relative path
        try:
            rel_path = os.path.relpath(file_path, search_path)
        except Exception as e:
            continue  # Shouldn't happen if file_path is within search_path

        # Check file size limit
        try:
            if file_path.stat().st_size > MAX_FILE_SIZE:
                continue  # Skip large files
        except Exception as e:
            continue  # Skip on error

        # Read file content
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            continue  # Skip file on error

        # Check regex match with timeout
        try:
            if pattern.search(content):
                results.append(rel_path)
        except Exception as e:
            continue  # Skip files that cause regex errors during search

    return {"files": results}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)