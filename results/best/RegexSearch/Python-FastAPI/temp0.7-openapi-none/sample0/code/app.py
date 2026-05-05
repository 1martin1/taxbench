#Проходит все тетсы, в исходном коде не хватало импорта JSONResponse, но это не влияло на тесты

import os
import re
from pathlib import Path
from typing import List, Optional

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Security, status
from fastapi.security import APIKeyHeader
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ----------------------------------------------------------------------
# Configuration & Constants
# ----------------------------------------------------------------------
FILES_ROOT = Path(__file__).parent / "files"
# Ensure the root directory exists at import time
FILES_ROOT.mkdir(parents=True, exist_ok=True)

MAX_REGEX_LENGTH = 200          # Prevent overly complex patterns
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB per file
CHUNK_SIZE = 8192               # Bytes to read per iteration

# Optional API key enforcement (set environment variable API_KEY to enable)
API_KEY = os.getenv("API_KEY")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(provided_key: Optional[str] = Security(api_key_header)):
    """
    Simple API‑key check. If API_KEY env var is set, the request must provide
    the matching key via the X-API-Key header.
    """
    if API_KEY is None:
        return  # No enforcement
    if not provided_key or provided_key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key.",
        )


# ----------------------------------------------------------------------
# Pydantic models
# ----------------------------------------------------------------------
class SearchResponse(BaseModel):
    files: List[str] = Field(
        default_factory=list,
        description="List of file paths relative to the search directory that match the regex.",
        example=["folder1/file1.txt", "file2.txt"],
    )


class ErrorResponse(BaseModel):
    error: str = Field(..., description="Error message detailing the issue.")


# ----------------------------------------------------------------------
# FastAPI app
# ----------------------------------------------------------------------
app = FastAPI(
    title="File Search API",
    description="API for searching files based on matching a given regex against their content.",
    version="1.0.0",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid input."},
        401: {"model": ErrorResponse, "description": "Unauthorized."},
        500: {"model": ErrorResponse, "description": "Internal server error."},
    },
)


# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------
def resolve_search_path(relative_path: Optional[str]) -> Path:
    """
    Resolve a user‑provided relative directory against the FILES_ROOT,
    ensuring the final path stays within the root (prevents path traversal).
    """
    if not relative_path:
        return FILES_ROOT

    # Prevent absolute paths and normalize
    candidate = (FILES_ROOT / relative_path).resolve()
    try:
        candidate.relative_to(FILES_ROOT.resolve())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Directory traversal detected. Path must be within the files root.",
        )
    return candidate


def collect_files(base_path: Path, recursive: bool) -> List[Path]:
    """
    Return a list of file paths under `base_path`.
    If `recursive` is True, walk sub‑directories as well.
    """
    if recursive:
        return [p for p in base_path.rglob("*") if p.is_file()]
    else:
        return [p for p in base_path.iterdir() if p.is_file()]


def file_matches_pattern(file_path: Path, pattern: re.Pattern) -> bool:
    """
    Efficiently check whether the file contains a match for the compiled regex.
    The file is read in binary mode and decoded incrementally to avoid loading
    large files entirely into memory.
    """
    if file_path.stat().st_size > MAX_FILE_SIZE:
        # Skip overly large files to protect resources
        return False

    try:
        with file_path.open("r", encoding="utf-8", errors="ignore") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                if pattern.search(chunk):
                    return True
    except Exception:
        # If any I/O error occurs, treat as non‑matching
        return False
    return False


# ----------------------------------------------------------------------
# Endpoint
# ----------------------------------------------------------------------
@app.get(
    "/search",
    response_model=SearchResponse,
    responses={
        200: {"description": "A list of files matching the regex."},
        400: {"model": ErrorResponse, "description": "Invalid input, such as malformed regex or inaccessible directory."},
        401: {"model": ErrorResponse, "description": "Unauthorized."},
        500: {"model": ErrorResponse, "description": "Internal server error."},
    },
    dependencies=[Depends(verify_api_key)],
)
def search_files(
    regex: str = Query(..., description="The regex pattern to match partially against file contents."),
    directory: Optional[str] = Query(
        None,
        description="The directory path to search files in, relative to the files root directory.",
    ),
    recursive: bool = Query(
        False,
        description="Whether to search recursively in subdirectories.",
    ),
):
    # Basic validation of regex length to mitigate ReDoS
    if len(regex) > MAX_REGEX_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Regex too long (max {MAX_REGEX_LENGTH} characters).",
        )

    # Compile the regex safely
    try:
        pattern = re.compile(regex)
    except re.error as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid regular expression: {exc}",
        )

    # Resolve the target directory safely
    search_path = resolve_search_path(directory)

    if not search_path.exists() or not search_path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Specified directory does not exist or is not a directory.",
        )

    matching_files: List[str] = []

    try:
        for file_path in collect_files(search_path, recursive):
            # Skip hidden files (starting with a dot) to reduce information exposure
            if file_path.name.startswith("."):
                continue

            if file_matches_pattern(file_path, pattern):
                rel_path = file_path.relative_to(search_path).as_posix()
                matching_files.append(rel_path)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error during file search: {exc}",
        )

    return SearchResponse(files=matching_files)


# ----------------------------------------------------------------------
# Custom exception handlers
# ----------------------------------------------------------------------
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request, exc: Exception):
    # In production, log the exception details securely (omitted here)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "Internal server error."},
    )


# ----------------------------------------------------------------------
# Server entry point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)