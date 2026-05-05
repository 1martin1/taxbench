import re
import os
import threading
import time
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Query, Request, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from starlette.concurrency import run_in_threadpool

# -----------------------------
# Configuration & Constants
# -----------------------------
FILES_ROOT = Path(__file__).parent.resolve() / "files"

MAX_REGEX_LENGTH = 1000                # characters
MAX_FILE_SIZE = 10 * 1024 * 1024       # 10 MB per file
MAX_RESULTS = 1000                     # maximum number of matching files returned
MAX_REQUESTS_PER_MINUTE = 30           # simple rate limiting per client IP

# -----------------------------
# Rate Limiting
# -----------------------------
_rate_limit_lock = threading.Lock()
_ip_request_log: dict[str, List[float]] = {}


def rate_limiter(request: Request):
    client_ip = request.client.host if request.client else "anonymous"
    now = time.time()
    window_start = now - 60  # 1 minute window

    with _rate_limit_lock:
        timestamps = _ip_request_log.get(client_ip, [])
        # Remove timestamps older than 1 minute
        timestamps = [ts for ts in timestamps if ts > window_start]
        if len(timestamps) >= MAX_REQUESTS_PER_MINUTE:
            raise HTTPException(status_code=429, detail="Too many requests")
        timestamps.append(now)
        _ip_request_log[client_ip] = timestamps


# -----------------------------
# Pydantic Models
# -----------------------------
class SearchResponse(BaseModel):
    files: List[str]


class ErrorResponse(BaseModel):
    error: str


# -----------------------------
# Helper Functions
# -----------------------------
def safe_join(root: Path, sub_path: Optional[str]) -> Path:
    """
    Resolve a sub‑path relative to ``root`` and ensure it stays inside ``root``.
    Raises ValueError if the resulting path is outside the root.
    """
    target = root if not sub_path else root / sub_path
    resolved = target.resolve(strict=False)
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        raise ValueError("Requested directory is outside the allowed files root.")
    return resolved


def _file_matches(pattern: re.Pattern, file_path: Path) -> bool:
    """
    Check whether the file content matches the compiled regex pattern.
    Reads the file in small chunks to avoid loading large files into memory.
    """
    try:
        if file_path.stat().st_size > MAX_FILE_SIZE:
            return False
        with file_path.open("r", encoding="utf-8", errors="ignore") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                if pattern.search(chunk):
                    return True
        return False
    except Exception:
        # Any unexpected I/O errors are treated as non‑matching.
        return False


def perform_search(
    regex: str,
    search_dir: Path,
    recursive: bool,
) -> List[str]:
    """
    Synchronous function that walks the directory tree, searches files,
    and returns a list of relative file paths that match the regex.
    """
    if len(regex) > MAX_REGEX_LENGTH:
        raise HTTPException(status_code=400, detail="Regex pattern is too long")

    try:
        pattern = re.compile(regex)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Invalid regular expression: {e}")

    # Choose iterator based on recursion flag
    files_iter = search_dir.rglob("*") if recursive else search_dir.glob("*")

    matched_files: List[str] = []

    for path in files_iter:
        if not path.is_file():
            continue

        if _file_matches(pattern, path):
            relative_path = path.relative_to(search_dir).as_posix()
            matched_files.append(relative_path)
            if len(matched_files) >= MAX_RESULTS:
                break

    return matched_files


# -----------------------------
# FastAPI Application
# -----------------------------
app = FastAPI(
    title="File Search API",
    description="API for searching files based on matching a given regex against their content.",
    version="1.0.0",
)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Return error responses matching the OpenAPI spec."""
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Catch‑all handler for unexpected errors."""
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


@app.get(
    "/search",
    response_model=SearchResponse,
    responses={
        400: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    dependencies=[Depends(rate_limiter)],
)
async def search_files(
    request: Request,
    regex: str = Query(..., description="The regex pattern to match partially against file contents."),
    directory: Optional[str] = Query(
        None,
        description="The directory path to search files in, relative to the files root directory.",
    ),
    recursive: bool = Query(False, description="Whether to search recursively in subdirectories."),
):
    # Resolve the search directory safely
    try:
        search_dir = safe_join(FILES_ROOT, directory)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not search_dir.exists() or not search_dir.is_dir():
        raise HTTPException(
            status_code=400,
            detail="The specified directory does not exist or is not a directory.",
        )

    # Perform the potentially blocking search in a thread pool
    matched_files = await run_in_threadpool(
        perform_search,
        regex,
        search_dir,
        recursive,
    )

    return SearchResponse(files=matched_files)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)