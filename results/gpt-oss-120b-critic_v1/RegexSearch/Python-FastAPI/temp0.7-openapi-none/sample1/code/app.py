import re
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Query, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(
    title="File Search API",
    description="API for searching files based on matching a given regex against their content.",
    version="1.0.0",
)

# Root directory where all searchable files are stored
FILES_ROOT = (Path(__file__).parent / "files").resolve()

# Security / resource limits
MAX_REGEX_LENGTH = 200               # characters
MAX_FILE_SIZE = 5 * 1024 * 1024      # 5 MiB per file
MAX_FILES_PROCESSED = 10_000         # total files examined per request


class FilesResponse(BaseModel):
    files: List[str]


class ErrorResponse(BaseModel):
    error: str


def _validate_and_resolve_directory(dir_param: Optional[str]) -> Path:
    """
    Resolve the requested directory relative to FILES_ROOT and ensure it stays inside the root.
    """
    target = FILES_ROOT if not dir_param else (FILES_ROOT / dir_param).resolve()

    # Prevent path traversal
    try:
        target.relative_to(FILES_ROOT)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Requested directory is outside of allowed files root.",
        ) from exc

    if not target.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Requested directory does not exist or is not a directory.",
        )
    return target


def _search_files(search_dir: Path, pattern: re.Pattern, recursive: bool) -> List[str]:
    """
    Walk through files in `search_dir` (recursively if requested) and return paths
    (relative to `search_dir`) of files whose content matches `pattern`.
    Limits are applied to prevent excessive resource consumption.
    """
    matched_files: List[str] = []
    processed_files = 0

    iterator = search_dir.rglob("*") if recursive else search_dir.iterdir()

    for entry in iterator:
        if processed_files >= MAX_FILES_PROCESSED:
            break  # stop processing to avoid DoS

        if not entry.is_file():
            continue

        processed_files += 1

        try:
            # Skip overly large files
            if entry.stat().st_size > MAX_FILE_SIZE:
                continue

            # Read file incrementally (line‑by‑line) to keep memory usage low
            with entry.open("r", errors="ignore") as f:
                for line in f:
                    if pattern.search(line):
                        rel_path = entry.relative_to(search_dir).as_posix()
                        matched_files.append(rel_path)
                        break  # no need to read the rest of this file
        except Exception:
            # Silently skip files that cannot be read for any reason
            continue

    return matched_files


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Return errors with an `error` field as defined in the OpenAPI spec."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Catch‑all handler that hides internal details."""
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "Internal server error"},
    )


@app.get(
    "/search",
    summary="Search files by regex",
    description=(
        "Searches files in a specified directory and returns files whose content "
        "matches the given regex."
    ),
    response_model=FilesResponse,
    responses={
        200: {
            "description": "A list of files matching the regex, specifying the path relative to the indicated directory.",
            "content": {"application/json": {"example": {"files": ["folder1/file1.txt", "file2.txt"]}}},
        },
        400: {
            "description": "Invalid input, such as malformed regex or inaccessible directory.",
            "model": ErrorResponse,
            "content": {"application/json": {"example": {"error": "Invalid regex pattern"}}},
        },
        500: {
            "description": "Internal server error.",
            "model": ErrorResponse,
            "content": {"application/json": {"example": {"error": "Internal server error"}}},
        },
    },
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
    # Basic sanity check for regex size to mitigate ReDoS
    if len(regex) > MAX_REGEX_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Regex pattern is too long.",
        )

    # Compile regex safely
    try:
        compiled_regex = re.compile(regex)
    except re.error as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid regex pattern: {exc}",
        )

    # Resolve target directory safely
    search_dir = _validate_and_resolve_directory(directory)

    # Perform the search
    matched = _search_files(search_dir, compiled_regex, recursive)

    return FilesResponse(files=matched)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)