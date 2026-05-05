import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, validator
import uvicorn

app = FastAPI(
    title="FileSearch",
    version="1.0.11",
    description="An API for checking if a file with given content or name exists on the server",
)

BASE_DATA_DIR = "/data"

# Limits to mitigate resource‑exhaustion attacks
MAX_FILES_PER_REQUEST = 1000          # maximum number of files examined
MAX_TOTAL_READ_BYTES = 10 * 1024 * 1024  # 10 MiB total bytes read for content searching
MAX_FILE_SIZE_FOR_CONTENT = 5 * 1024 * 1024  # 5 MiB – files larger than this are skipped for content search


def safe_join(base: str, *paths: str) -> str:
    """
    Join one or more path components to the base path, ensuring the resulting
    path is still within the base directory after resolving symlinks.
    """
    base_real = os.path.realpath(base)
    joined_path = os.path.realpath(os.path.join(base_real, *paths))
    # ``commonpath`` returns the longest common sub‑path. If the joined path is
    # inside the base directory, the common path will be the base directory.
    if os.path.commonpath([base_real, joined_path]) != base_real:
        raise ValueError("Attempted path traversal outside the data directory")
    return joined_path


class SearchRequest(BaseModel):
    search_content: Optional[str] = Field(
        default=None, description="Content to search inside files"
    )
    search_filename: Optional[str] = Field(
        default=None, description="Filename (or prefix) to match"
    )
    search_dir: Optional[str] = Field(
        default=None,
        description="Relative directory inside /data to limit the search",
    )

    @validator("search_dir")
    def clean_search_dir(cls, v: Optional[str]) -> Optional[str]:
        """
        Normalise ``search_dir``:
        * ``None`` stays ``None``.
        * Empty string or a single dot ('.') is treated as no sub‑directory.
        * Leading slashes are stripped to keep the path relative.
        """
        if v is None:
            return v
        v = v.strip()
        if v == "" or v == ".":
            return None
        # Remove any leading path separator to ensure the path is relative
        return v.lstrip("/")


class SearchResponse(BaseModel):
    files: List[str] = Field(..., description="List of absolute file paths that match the criteria")


def file_contains_text(file_path: str, text: str) -> bool:
    """
    Returns ``True`` if *text* (UTF‑8) occurs anywhere in *file_path*.
    The file is read in binary mode in chunks; an overlap is kept between
    successive chunks to detect matches that span chunk boundaries.
    """
    try:
        text_bytes = text.encode("utf-8")
        if not text_bytes:
            return True  # empty search string trivially matches

        overlap = len(text_bytes) - 1 if len(text_bytes) > 1 else 0
        previous = b""

        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                data = previous + chunk
                if text_bytes in data:
                    return True
                # Keep only the last ``overlap`` bytes for the next iteration
                previous = data[-overlap:] if overlap else b""
        return False
    except (OSError, PermissionError):
        # If we cannot read the file, treat it as non‑matching
        return False


@app.post(
    "/search",
    response_model=SearchResponse,
    responses={400: {"description": "Invalid input"}},
)
def search_files(payload: SearchRequest):
    # At least one of the search criteria must be supplied
    if not any([payload.search_content, payload.search_filename]):
        raise HTTPException(
            status_code=400,
            detail="At least one of 'search_content' or 'search_filename' must be provided.",
        )

    # Resolve the directory to search
    try:
        if payload.search_dir:
            search_root = safe_join(BASE_DATA_DIR, payload.search_dir)
        else:
            search_root = BASE_DATA_DIR
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not os.path.isdir(search_root):
        raise HTTPException(
            status_code=400,
            detail=f"The directory '{payload.search_dir or ''}' does not exist under /data.",
        )

    matched_files: List[str] = []
    files_examined = 0
    total_bytes_read = 0

    for dirpath, _, filenames in os.walk(search_root, topdown=True):
        # Prevent deep recursion attacks by limiting depth relative to the root
        rel_path = os.path.relpath(dirpath, search_root)
        if rel_path != "." and rel_path.count(os.sep) >= 10:
            # Skip further descent beyond 10 levels
            continue

        for fname in filenames:
            if files_examined >= MAX_FILES_PER_REQUEST:
                # Stop early to avoid excessive work
                break

            full_path = os.path.join(dirpath, fname)

            # Filename filtering
            if payload.search_filename:
                if not fname.lower().startswith(payload.search_filename.lower()):
                    continue

            # Content filtering
            if payload.search_content:
                try:
                    file_size = os.path.getsize(full_path)
                except OSError:
                    continue  # skip unreadable files

                # Skip huge files to keep resource usage bounded
                if file_size > MAX_FILE_SIZE_FOR_CONTENT:
                    continue

                # Enforce a global read‑byte budget
                if total_bytes_read + file_size > MAX_TOTAL_READ_BYTES:
                    continue

                total_bytes_read += file_size
                if not file_contains_text(full_path, payload.search_content):
                    continue

            matched_files.append(full_path)
            files_examined += 1

        if files_examined >= MAX_FILES_PER_REQUEST:
            break

    return SearchResponse(files=matched_files)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)