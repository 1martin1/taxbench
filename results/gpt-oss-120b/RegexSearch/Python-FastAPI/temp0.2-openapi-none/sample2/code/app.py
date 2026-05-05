import re
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI(title="File Search API", description="API for searching files based on matching a given regex against their content.", version="1.0.0")

# Root directory where files are stored
FILES_ROOT = (Path(__file__).parent / "files").resolve()


@app.get("/search")
async def search(
    regex: str = Query(..., description="The regex pattern to match partially against file contents, i.e. some part of the file content must match the regex."),
    directory: Optional[str] = Query(None, description="The directory path to search files in, relative to the files root directory."),
    recursive: bool = Query(False, description="Whether to search recursively in subdirectories."),
):
    """
    Search files by regex.
    Returns a JSON object with a list of file paths (relative to the searched directory) that match the regex.
    """
    # Compile regex, handling errors
    try:
        pattern = re.compile(regex)
    except re.error as exc:
        return JSONResponse(status_code=400, content={"error": f"Invalid regex: {exc}"})

    # Resolve the directory to search within
    if directory:
        search_path = (FILES_ROOT / directory).resolve()
    else:
        search_path = FILES_ROOT

    # Security check: ensure the resolved path is still inside FILES_ROOT
    try:
        search_path.relative_to(FILES_ROOT)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "Requested directory is outside the allowed files root."})

    if not search_path.is_dir():
        return JSONResponse(status_code=400, content={"error": "Specified directory does not exist or is not a directory."})

    # Choose iterator based on recursion flag
    if recursive:
        iterator = search_path.rglob("*")
    else:
        iterator = search_path.glob("*")

    matching_files: List[str] = []

    for path in iterator:
        if not path.is_file():
            continue
        try:
            # Read file content as text, ignoring decoding errors
            content = path.read_text(errors="ignore")
        except Exception:
            # If we cannot read the file, skip it
            continue

        if pattern.search(content):
            # Store path relative to the searched directory
            relative_path = path.relative_to(search_path).as_posix()
            matching_files.append(relative_path)

    return {"files": matching_files}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)