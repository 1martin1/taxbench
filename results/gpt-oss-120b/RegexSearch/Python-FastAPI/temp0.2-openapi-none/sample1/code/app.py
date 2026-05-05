import re
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="File Search API", description="API for searching files based on matching a given regex against their content.", version="1.0.0")

FILES_ROOT = Path(__file__).parent / "files"


class SearchResponse(BaseModel):
    files: List[str]


class ErrorResponse(BaseModel):
    error: str


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """
    Return errors in the format required by the OpenAPI spec:
    { "error": "message" }
    """
    if exc.status_code in (400, 500):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get(
    "/search",
    response_model=SearchResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    summary="Search files by regex",
    description="Searches files in a specified directory and returns files whose content matches the given regex.",
)
async def search_files(
    regex: str = Query(..., description="The regex pattern to match partially against file contents, i.e. some part of the file content must match the regex.", example="hello\\s(you|world)"),
    directory: Optional[str] = Query(None, description="The directory path to search files in, relative to the files root directory.", example="subdir"),
    recursive: bool = Query(False, description="Whether to search recursively in subdirectories.", example=False),
):
    # Verify the files root exists
    if not FILES_ROOT.is_dir():
        raise HTTPException(status_code=500, detail="Files root directory does not exist.")

    # Resolve the target search directory
    search_dir = FILES_ROOT
    if directory:
        candidate = FILES_ROOT / directory
        try:
            candidate = candidate.resolve(strict=False)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid directory path.")
        # Prevent directory traversal attacks
        try:
            candidate.relative_to(FILES_ROOT.resolve())
        except ValueError:
            raise HTTPException(status_code=400, detail="Directory traversal is not allowed.")
        search_dir = candidate

    if not search_dir.is_dir():
        raise HTTPException(status_code=400, detail="Specified directory does not exist or is not a directory.")

    # Compile the regex pattern
    try:
        pattern = re.compile(regex)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {e}")

    # Choose glob pattern based on recursion flag
    glob_pattern = "**/*" if recursive else "*"

    matching_files: List[str] = []
    for path in search_dir.glob(glob_pattern):
        if not path.is_file():
            continue
        try:
            # Read file content as text, ignoring decoding errors
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            # If we cannot read the file, skip it
            continue

        if pattern.search(content):
            relative_path = path.relative_to(search_dir).as_posix()
            matching_files.append(relative_path)

    return {"files": matching_files}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)