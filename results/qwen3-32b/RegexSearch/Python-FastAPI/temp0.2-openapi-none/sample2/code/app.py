from fastapi import FastAPI, HTTPException, Query
from pathlib import Path
import re

app = FastAPI()

BASE_DIR = Path("files").resolve()

@app.get("/search")
async def search_files(
    regex: str,
    directory: str = Query(None),
    recursive: bool = Query(False)
):
    try:
        pattern = re.compile(regex)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {e}")

    if directory is None:
        search_dir = BASE_DIR
    else:
        search_dir = BASE_DIR / directory
        if not search_dir.is_relative_to(BASE_DIR):
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