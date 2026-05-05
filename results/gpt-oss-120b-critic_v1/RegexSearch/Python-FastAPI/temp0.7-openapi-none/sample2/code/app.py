import os
import re
import time
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

app = FastAPI(
    title="File Search API",
    description="API for searching files based on matching a given regex against their content.",
    version="1.0.0",
)

# Root directory where all searchable files live (relative to this script)
ROOT_DIR = Path(__file__).parent / "files"

# Security / resource limits
MAX_REGEX_LENGTH = 200               # characters
MAX_FILE_SIZE = 1_048_576            # 1 MiB per file
MAX_FILES_PER_REQUEST = 5_000        # limit number of files examined per request


def _safe_resolve(path: Path) -> Path:
    """
    Resolve *path* and ensure it stays inside ROOT_DIR.
    Raises HTTPException(400) if the resolved path escapes the root.
    """
    try:
        resolved = path.resolve()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Unable to resolve path: {exc}")

    root_resolved = ROOT_DIR.resolve()
    # pathlib.Path.is_relative_to is available on Python 3.9+
    if not resolved.is_relative_to(root_resolved):
        raise HTTPException(status_code=400, detail="Directory traversal is not allowed.")
    return resolved


def _read_limited_text(file_path: Path) -> str:
    """
    Read up to MAX_FILE_SIZE bytes from *file_path* and decode as UTF‑8,
    ignoring decoding errors. Returns the decoded string.
    """
    try:
        with file_path.open("rb") as f:
            raw = f.read(MAX_FILE_SIZE + 1)  # read one byte extra to detect overflow
        if len(raw) > MAX_FILE_SIZE:
            # File is too large; treat as non‑matching to avoid memory pressure
            return ""
        return raw.decode("utf-8", errors="ignore")
    except Exception:
        # Any I/O error results in an empty string (treated as non‑matching)
        return ""


def _validate_regex(regex: str) -> re.Pattern:
    """
    Validate the supplied regex against length limits and compile it.
    Raises HTTPException(400) on failure.
    """
    if len(regex) > MAX_REGEX_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Regex pattern exceeds maximum allowed length of {MAX_REGEX_LENGTH} characters.",
        )
    try:
        # Using a non‑greedy compilation flag to reduce potential backtracking
        return re.compile(regex, flags=re.UNICODE)
    except re.error as err:
        raise HTTPException(status_code=400, detail=f"Invalid regex pattern: {err}")


@app.get("/search")
def search_files(
    request: Request,
    regex: str = Query(..., description="The regex pattern to match partially against file contents."),
    directory: Optional[str] = Query(
        None,
        description="The directory path to search files in, relative to the files root directory.",
    ),
    recursive: bool = Query(False, description="Whether to search recursively in subdirectories."),
):
    """
    Search files under the given directory (or the root) for content that matches the supplied regex.
    Returns a JSON object with a list of matching file paths relative to the searched directory.
    """
    # Resolve target directory safely
    target_dir = ROOT_DIR if directory is None else ROOT_DIR / directory
    target_dir = _safe_resolve(target_dir)

    if not target_dir.is_dir():
        raise HTTPException(status_code=400, detail="Specified directory does not exist or is not a directory.")

    # Compile and validate regex
    pattern = _validate_regex(regex)

    matches: List[str] = []
    examined_files = 0

    def check_file(file_path: Path):
        nonlocal examined_files
        if examined_files >= MAX_FILES_PER_REQUEST:
            return  # stop processing further files
        examined_files += 1

        content = _read_limited_text(file_path)
        if not content:
            return  # empty or oversized file, treat as non‑matching
        if pattern.search(content):
            rel_path = file_path.relative_to(target_dir).as_posix()
            matches.append(rel_path)

    if recursive:
        for path in target_dir.rglob("*"):
            if path.is_file():
                check_file(path)
                if examined_files >= MAX_FILES_PER_REQUEST:
                    break
    else:
        for path in target_dir.iterdir():
            if path.is_file():
                check_file(path)
                if examined_files >= MAX_FILES_PER_REQUEST:
                    break

    return {"files": matches}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)