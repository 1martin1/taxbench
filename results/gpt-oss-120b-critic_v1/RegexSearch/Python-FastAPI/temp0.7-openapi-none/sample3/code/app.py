import os
import re
from pathlib import Path
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query, status
from fastapi.responses import JSONResponse

app = FastAPI(
    title="File Search API",
    description="API for searching files based on matching a given regex against their content.",
    version="1.0.0",
)

# ---------------------------------------------------------------------------
# Configuration / limits (adjust as needed for security)
# ---------------------------------------------------------------------------
MAX_REGEX_LENGTH = 200               # characters
MAX_FILE_SIZE = 2 * 1024 * 1024      # 2 MiB per file
MAX_FILES = 10_000                   # maximum files processed per request
READ_CHUNK_SIZE = 64 * 1024          # 64 KiB

# Resolve the files root directory safely, handling environments where __file__
# may be undefined (e.g., interactive sessions or zipapps).
try:
    BASE_DIR = Path(__file__).parent.resolve()
except NameError:
    BASE_DIR = Path.cwd()
FILES_ROOT = (BASE_DIR / "files").resolve()

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def safe_join(root: Path, *paths: str) -> Path:
    """
    Join *paths* to *root* and ensure the result stays within *root*.
    Prevents directory‑traversal attacks.
    """
    new_path = (root / Path(*paths)).resolve()
    try:
        new_path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provided directory escapes the files root.",
        ) from exc
    return new_path


def iter_files(base_dir: Path, recursive: bool):
    """
    Yield file paths under *base_dir* respecting the global MAX_FILES limit.
    """
    count = 0
    if recursive:
        for path in base_dir.rglob("*"):
            if path.is_file():
                yield path
                count += 1
                if count >= MAX_FILES:
                    break
    else:
        for path in base_dir.iterdir():
            if path.is_file():
                yield path
                count += 1
                if count >= MAX_FILES:
                    break


def file_matches_pattern(file_path: Path, pattern: re.Pattern) -> bool:
    """
    Search *pattern* in *file_path* without loading the whole file into memory.
    Files larger than MAX_FILE_SIZE are skipped to avoid OOM.
    """
    try:
        if file_path.stat().st_size > MAX_FILE_SIZE:
            return False
    except OSError:
        return False  # Unable to stat the file; treat as non‑matching.

    try:
        with file_path.open("rb") as f:
            # Read the file in binary chunks and decode incrementally.
            # Using 'replace' ensures we never raise decoding errors.
            decoder = lambda b: b.decode("utf-8", errors="replace")
            buffer = ""
            while True:
                chunk = f.read(READ_CHUNK_SIZE)
                if not chunk:
                    break
                buffer += decoder(chunk)
                # Search in the current buffer.
                if pattern.search(buffer):
                    return True
                # Keep a tail to allow matches that span across chunks.
                # Retain the last 1024 characters (arbitrary safe size).
                buffer = buffer[-1024:]
        return False
    except Exception:
        # Any I/O or decoding issue results in the file being ignored.
        return False


# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------

@app.get(
    "/search",
    response_model=dict,
    responses={
        200: {"description": "A list of files matching the regex."},
        400: {"description": "Invalid input, such as malformed regex or inaccessible directory."},
        500: {"description": "Internal server error."},
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
    # -----------------------------------------------------------------------
    # Validate regex length to mitigate ReDoS attacks
    # -----------------------------------------------------------------------
    if len(regex) > MAX_REGEX_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Regex pattern too long (max {MAX_REGEX_LENGTH} characters).",
        )

    # Compile regex; any compilation error is reported as a client error.
    try:
        pattern = re.compile(regex)
    except re.error as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid regular expression: {exc}",
        ) from exc

    # Resolve the target directory safely.
    target_dir = FILES_ROOT if directory is None else safe_join(FILES_ROOT, directory)

    if not target_dir.exists() or not target_dir.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Specified directory does not exist or is not a directory.",
        )

    matching_files: List[str] = []
    try:
        for file_path in iter_files(target_dir, recursive):
            if file_matches_pattern(file_path, pattern):
                # Path relative to the *search* directory, using POSIX separators.
                relative_path = file_path.relative_to(target_dir).as_posix()
                matching_files.append(relative_path)
    except Exception as exc:
        # Unexpected errors are logged (if a logger existed) and returned as 500.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal server error occurred.",
        ) from exc

    return {"files": matching_files}


# ---------------------------------------------------------------------------
# Application startup
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Ensure the files root exists to avoid confusing errors.
    os.makedirs(FILES_ROOT, exist_ok=True)
    uvicorn.run(app, host="0.0.0.0", port=5000)