import re
import sys
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(
    title="File Search API",
    description="API for searching files based on matching a given regex against their content.",
    version="1.0.0",
)

# Root directory where files are stored. It will be created on first request if missing.
FILES_ROOT = Path(__file__).parent / "files"

# Security / resource limits
MAX_REGEX_LENGTH = 500          # characters
MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MiB per file


class SearchResponse(BaseModel):
    files: List[str]


class ErrorResponse(BaseModel):
    error: str


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """
    Return errors strictly in the format required by the OpenAPI spec:
    { "error": "message" }
    """
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """
    Catch‑all handler for unexpected errors. Do not expose internal details.
    """
    # Log the exception to stderr for debugging/ops purposes.
    print(f"Unhandled exception: {exc}", file=sys.stderr)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error."},
    )


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
    regex: str = Query(
        ...,
        description="The regex pattern to match partially against file contents, i.e. some part of the file content must match the regex.",
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
    # Ensure the files root exists; create it lazily if missing.
    if not FILES_ROOT.is_dir():
        try:
            FILES_ROOT.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise HTTPException(status_code=500, detail="Unable to create files root directory.") from e

    # Resolve the target search directory safely.
    search_dir = FILES_ROOT
    if directory:
        candidate = FILES_ROOT / directory
        # Resolve without requiring existence to avoid race conditions.
        candidate = candidate.resolve(strict=False)
        # Prevent directory traversal attacks.
        try:
            candidate.relative_to(FILES_ROOT.resolve())
        except ValueError:
            raise HTTPException(status_code=400, detail="Directory traversal is not allowed.")
        search_dir = candidate

    if not search_dir.is_dir():
        raise HTTPException(status_code=400, detail="Specified directory does not exist or is not a directory.")

    # Basic validation of the regex to mitigate ReDoS.
    if len(regex) > MAX_REGEX_LENGTH:
        raise HTTPException(status_code=400, detail=f"Regex pattern exceeds maximum length of {MAX_REGEX_LENGTH} characters.")
    try:
        pattern = re.compile(regex)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {e}")

    # Choose glob pattern based on recursion flag.
    glob_pattern = "**/*" if recursive else "*"

    matching_files: List[str] = []
    for path in search_dir.glob(glob_pattern):
        if not path.is_file():
            continue

        # Skip files that are too large.
        try:
            if path.stat().st_size > MAX_FILE_SIZE:
                continue
        except Exception:
            # If we cannot stat the file, skip it.
            continue

        try:
            # Read file content as text, ignoring decoding errors.
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            # If we cannot read the file, skip it.
            continue

        if pattern.search(content):
            relative_path = path.relative_to(search_dir).as_posix()
            matching_files.append(relative_path)

    return {"files": matching_files}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)