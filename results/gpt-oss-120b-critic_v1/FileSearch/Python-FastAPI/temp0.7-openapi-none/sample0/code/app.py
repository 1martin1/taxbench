import os
from pathlib import Path
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(
    title="FileSearch",
    version="1.0.11",
    description="An API for checking if a file with given content or name exists on the server",
)

# Base directory where searchable files are stored.
BASE_DATA_DIR = Path("/data").resolve()

# Limits to mitigate resource‑exhaustion attacks.
MAX_FILE_SIZE = 10 * 1024 * 1024          # 10 MiB per file
MAX_FILES_SCANNED = 10_000                # maximum files examined per request
CHUNK_SIZE = 8192                         # bytes read per iteration


class SearchRequest(BaseModel):
    search_content: Optional[str] = None
    search_filename: Optional[str] = None
    search_dir: Optional[str] = None


def _is_subpath(base: Path, target: Path) -> bool:
    """
    Return True if *target* is a sub‑path of *base*.
    Both paths must be absolute and resolved.
    """
    try:
        target.relative_to(base)
        return True
    except ValueError:
        return False


def _safe_join(base: Path, sub_path: str) -> Path:
    """
    Join *sub_path* to *base* and ensure the result stays within *base*.
    Reject absolute paths and paths that escape the base directory.
    """
    # Disallow absolute paths – they would ignore the base directory.
    if os.path.isabs(sub_path):
        raise HTTPException(
            status_code=400,
            detail="search_dir must be a relative path.",
        )

    # Resolve the combined path to eliminate '..' components.
    joined = (base / sub_path).resolve()

    if not _is_subpath(base, joined):
        raise HTTPException(
            status_code=400,
            detail="Invalid search_dir; path traversal is not allowed.",
        )
    return joined


def _matches_filename(file_path: Path, pattern: str) -> bool:
    """Case‑insensitive substring match against the file name."""
    return pattern.lower() in file_path.name.lower()


def _contains_content(file_path: Path, content: str) -> bool:
    """
    Search *content* (as UTF‑8 bytes) inside *file_path* without loading the
    whole file into memory. Files larger than ``MAX_FILE_SIZE`` are skipped.
    """
    content_bytes = content.encode("utf-8")
    content_len = len(content_bytes)

    try:
        file_size = file_path.stat().st_size
        if file_size > MAX_FILE_SIZE:
            # Skip overly large files to protect the server.
            return False

        with file_path.open("rb") as f:
            # Keep a small overlap between chunks to catch matches spanning chunks.
            overlap = b""
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                data = overlap + chunk
                if content_bytes in data:
                    return True
                # Preserve the last (content_len - 1) bytes for the next iteration.
                overlap = data[-(content_len - 1) :] if content_len > 1 else b""
        return False
    except (OSError, PermissionError):
        # If the file cannot be read, treat it as non‑matching.
        return False


@app.post(
    "/search",
    summary="Retrieve a list of files from the server that contain the specified content or match the filename.",
)
async def search_files(request: SearchRequest):
    if not request.search_content and not request.search_filename:
        raise HTTPException(
            status_code=400,
            detail="At least one of 'search_content' or 'search_filename' must be provided.",
        )

    # Resolve the directory to search.
    target_dir = BASE_DATA_DIR
    if request.search_dir:
        target_dir = _safe_join(BASE_DATA_DIR, request.search_dir)

    if not target_dir.is_dir():
        raise HTTPException(
            status_code=400,
            detail="The specified search_dir does not exist or is not a directory.",
        )

    matched_files: List[str] = []
    files_scanned = 0

    # Walk the directory tree without following symbolic links.
    for root, dirs, files in os.walk(target_dir, followlinks=False):
        # Resolve the current root to ensure it stays inside BASE_DATA_DIR.
        current_root = Path(root).resolve()
        if not _is_subpath(BASE_DATA_DIR, current_root):
            # This should never happen, but we guard against it.
            continue

        for filename in files:
            if files_scanned >= MAX_FILES_SCANNED:
                raise HTTPException(
                    status_code=400,
                    detail="File scan limit exceeded; refine your search criteria.",
                )
            files_scanned += 1

            file_path = current_root / filename

            # Resolve the file path to prevent symlink escape.
            try:
                resolved_path = file_path.resolve()
            except OSError:
                # If resolution fails, skip the file.
                continue

            if not _is_subpath(BASE_DATA_DIR, resolved_path):
                # Skip files that resolve outside the allowed base directory.
                continue

            # Filename filter
            if request.search_filename:
                if not _matches_filename(resolved_path, request.search_filename):
                    continue

            # Content filter
            if request.search_content:
                if not _contains_content(resolved_path, request.search_content):
                    continue

            matched_files.append(str(resolved_path))

    return {"files": matched_files}


if __name__ == "__main__":
    # Run the application on 0.0.0.0:5000
    uvicorn.run(app, host="0.0.0.0", port=5000)