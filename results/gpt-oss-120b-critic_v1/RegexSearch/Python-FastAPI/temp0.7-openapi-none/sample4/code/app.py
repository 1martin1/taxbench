import os
import re
from pathlib import Path
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(
    title="File Search API",
    description="API for searching files based on matching a given regex against their content.",
    version="1.0.0",
)

# ---- Configuration constants ----
ROOT_DIR = Path(__file__).parent.resolve() / "files"
ROOT_DIR.mkdir(parents=True, exist_ok=True)  # Ensure the root exists at import time

MAX_REGEX_LENGTH = 200               # characters
MAX_FILE_SIZE = 2 * 1024 * 1024      # 2 MiB per file
MAX_FILES_PROCESSED = 10_000         # maximum files to examine per request

# ---- Pydantic models ----
class SearchResult(BaseModel):
    files: List[str]


class ErrorResponse(BaseModel):
    error: str


def _secure_path(base: Path, target: Path) -> Path:
    """
    Resolve *target* against *base* and ensure the result stays within *base*.
    Reject absolute *target* values outright.
    """
    if target.is_absolute():
        raise HTTPException(status_code=400, detail="Absolute paths are not allowed.")
    resolved = (base / target).resolve()
    try:
        resolved.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=400, detail="Directory traversal is not allowed.")
    return resolved


@app.get(
    "/search",
    response_model=SearchResult,
    responses={
        200: {"description": "A list of files matching the regex."},
        400: {"model": ErrorResponse, "description": "Invalid input."},
        500: {"model": ErrorResponse, "description": "Internal server error."},
    },
    summary="Search files by regex",
    description="Searches files in a specified directory and returns files whose content matches the given regex.",
)
async def search(
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
    # ---- Validate regex length ----
    if len(regex) > MAX_REGEX_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Regex pattern is too long (>{MAX_REGEX_LENGTH} characters).",
        )

    # Compile regex to validate syntax early
    try:
        pattern = re.compile(regex)
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid regex pattern: {exc}")

    # Resolve the target directory securely
    try:
        search_dir = _secure_path(ROOT_DIR, Path(directory)) if directory else ROOT_DIR
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Unable to resolve directory: {exc}")

    if not search_dir.is_dir():
        raise HTTPException(
            status_code=400,
            detail="Specified directory does not exist or is not a directory.",
        )

    matched_files: List[str] = []
    processed_files = 0

    try:
        iterator = search_dir.rglob("*") if recursive else search_dir.iterdir()

        for path in iterator:
            if not path.is_file():
                continue

            processed_files += 1
            if processed_files > MAX_FILES_PROCESSED:
                raise HTTPException(
                    status_code=400,
                    detail=f"Too many files to process (limit is {MAX_FILES_PROCESSED}).",
                )

            # Skip files that are larger than the allowed size
            try:
                if path.stat().st_size > MAX_FILE_SIZE:
                    continue
            except OSError:
                # If we cannot stat the file, skip it
                continue

            # Read up to MAX_FILE_SIZE bytes to avoid loading huge files into memory
            try:
                with path.open("r", errors="ignore") as f:
                    content = f.read(MAX_FILE_SIZE)
            except Exception:
                continue  # Unable to read; skip the file

            if pattern.search(content):
                relative_path = path.relative_to(search_dir).as_posix()
                matched_files.append(relative_path)

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Server error while searching files: {exc}",
        )

    return SearchResult(files=matched_files)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Return JSON error response for HTTPException."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail if isinstance(exc.detail, str) else str(exc.detail)},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Catch‑all handler for unexpected errors."""
    return JSONResponse(
        status_code=500,
        content={"error": f"Unexpected server error: {exc}"},
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)