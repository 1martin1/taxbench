from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional
from pathlib import Path
import re

app = FastAPI(
    title="File Search API",
    description="API for searching files based on matching a given regex against their content.",
    version="1.0.0",
)

# Root directory where all searchable files are stored
ROOT_DIR = Path(__file__).parent / "files"


class SearchResponse(BaseModel):
    files: List[str]


class ErrorResponse(BaseModel):
    error: str


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Return errors in the format required by the OpenAPI schema."""
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.get(
    "/search",
    response_model=SearchResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
def search_files(
    regex: str = Query(
        ...,
        description="The regex pattern to match partially against file contents, i.e. some part of the file content must match the regex.",
        example="hello\\s(you|world)",
    ),
    directory: Optional[str] = Query(
        None,
        description="The directory path to search files in, relative to the files root directory.",
    ),
    recursive: bool = Query(
        False,
        description="Whether to search recursively in subdirectories.",
    ),
):
    """
    Search files under the given directory (relative to the root `./files`) and return
    paths of files whose content matches the supplied regular expression.
    """
    # Resolve the target directory safely
    try:
        target_dir = ROOT_DIR if directory is None else (ROOT_DIR / directory)
        target_dir = target_dir.resolve()
        # Prevent directory traversal attacks
        if not str(target_dir).startswith(str(ROOT_DIR.resolve())):
            raise HTTPException(status_code=400, detail="Directory traversal is not allowed.")
        if not target_dir.is_dir():
            raise HTTPException(status_code=400, detail="Specified directory does not exist or is not a directory.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid directory parameter: {e}")

    # Compile the regular expression
    try:
        pattern = re.compile(regex)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Invalid regex pattern: {e}")

    matched_files: List[str] = []

    try:
        # Choose iterator based on recursion flag
        iterator = target_dir.rglob("*") if recursive else target_dir.glob("*")
        for path in iterator:
            if not path.is_file():
                continue
            try:
                # Read file content as text, ignoring decode errors
                content = path.read_text(errors="ignore")
            except Exception:
                # If a file cannot be read, skip it
                continue
            if pattern.search(content):
                # Path relative to the searched directory
                rel_path = path.relative_to(target_dir).as_posix()
                matched_files.append(rel_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Server error while searching files: {e}")

    return SearchResponse(files=matched_files)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)