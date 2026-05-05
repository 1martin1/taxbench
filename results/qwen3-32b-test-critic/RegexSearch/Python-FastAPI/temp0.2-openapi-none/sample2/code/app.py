from fastapi import FastAPI, HTTPException, Query
from pathlib import Path
import re
import threading

app = FastAPI()

BASE_DIR = Path(".").resolve()
allowed_root = BASE_DIR / "files"

def compile_with_timeout(pattern, timeout=1):
    result = None
    exception = None
    def target():
        nonlocal result, exception
        try:
            result = re.compile(pattern)
        except re.error as e:
            exception = e
    thread = threading.Thread(target=target)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError("Regex compilation timeout")
    if exception:
        raise re.error(str(exception))
    return result

@app.get("/search")
async def search_files(
    regex: str,
    directory: str = Query(None),
    recursive: bool = Query(False)
):
    try:
        pattern = compile_with_timeout(regex, timeout=1)
    except (re.error, TimeoutError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {e}")

    if directory is None:
        search_dir = allowed_root
    else:
        search_dir = BASE_DIR / directory
        if not search_dir.is_relative_to(allowed_root):
            raise HTTPException(status_code=400, detail="Directory is outside the allowed files root.")

    if not search_dir.exists() or not search_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"Directory {search_dir} does not exist or is not accessible.")

    try:
        if recursive:
            files = [f for f in search_dir.rglob("*") if f.is_file()]
        else:
            files = [f for f in search_dir.iterdir() if f.is_file()]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error accessing directory: {e}")

    matching_files = []
    for file in files:
        try:
            with open(file, "r", encoding="utf-8") as f:
                content = f.read()
            if pattern.search(content):
                rel_path = file.relative_to(search_dir)
                matching_files.append(str(rel_path))
        except Exception:
            pass

    return {"files": matching_files}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)