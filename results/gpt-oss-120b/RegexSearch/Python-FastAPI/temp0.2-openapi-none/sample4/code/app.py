import re
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
import uvicorn

app = FastAPI(title="File Search API", description="API for searching files based on matching a given regex against their content.", version="1.0.0")

# Root directory where all searchable files are stored
ROOT_DIR = (Path(__file__).parent / "files").resolve()


def _is_subpath(child: Path, parent: Path) -> bool:
    """Return True if *child* is a sub‑path of *parent*."""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


@app.get("/search", summary="Search files by regex", description="Searches files in a specified directory and returns files whose content matches the given regex.")
def search(
    regex: str = Query(..., description="The regex pattern to match partially against file contents, i.e. some part of the file content must match the regex.", example="hello\\s(you|world)"),
    directory: Optional[str] = Query(None, description="The directory path to search files in, relative to the files root directory."),
    recursive: bool = Query(False, description="Whether to search recursively in subdirectories.", ),
) -> dict:
    # Compile the regex, handling malformed patterns
    try:
        pattern = re.compile(regex)
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid regex pattern: {exc}")

    # Determine the directory to search
    search_dir = ROOT_DIR
    if directory:
        candidate = (ROOT_DIR / directory).resolve()
        if not _is_subpath(candidate, ROOT_DIR):
            raise HTTPException(status_code=400, detail="Directory traversal is not allowed.")
        if not candidate.is_dir():
            raise HTTPException(status_code=400, detail="Specified directory does not exist or is not a directory.")
        search_dir = candidate

    # Choose the appropriate iterator based on recursion flag
    if recursive:
        paths = search_dir.rglob("*")
    else:
        paths = search_dir.glob("*")

    matched_files: List[str] = []
    for path in paths:
        if not path.is_file():
            continue
        try:
            # Read file content as text; ignore decoding errors
            content = path.read_text(errors="ignore")
        except Exception:
            # If a file cannot be read, skip it
            continue

        if pattern.search(content):
            # Return paths relative to the searched directory, using POSIX style
            rel_path = path.relative_to(search_dir).as_posix()
            matched_files.append(rel_path)

    return {"files": matched_files}


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    # Return JSON error structure matching the OpenAPI spec
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request, exc: Exception):
    # Unexpected errors become 500 responses
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error."},
    )


# Required import for JSONResponse
from fastapi.responses import JSONResponse

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)