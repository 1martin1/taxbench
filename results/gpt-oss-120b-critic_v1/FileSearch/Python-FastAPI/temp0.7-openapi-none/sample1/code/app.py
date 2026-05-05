import os
from pathlib import Path
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, validator

app = FastAPI(
    title="FileSearch",
    version="1.0.11",
    description="An API for checking if a file with given content or name exists on the server",
)

# Base directory where files are stored
BASE_DIR = Path("/data").resolve()

# Resource limits (adjust as needed)
MAX_SEARCH_DEPTH = 5               # maximum directory depth relative to the start directory
MAX_FILES_SCANNED = 10_000         # maximum number of files examined per request
MAX_TOTAL_READ_BYTES = 10 * 1024 * 1024  # 10 MiB total bytes read from files per request
MAX_MATCHES = 1_000                # maximum number of matches returned


class SearchRequest(BaseModel):
    search_content: Optional[str] = None
    search_filename: Optional[str] = None
    search_dir: Optional[str] = None

    @validator("search_content", "search_filename", pre=True, always=True)
    def non_empty_string(cls, v):
        """
        Treat empty strings (after stripping) as not provided.
        """
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v


class SearchResponse(BaseModel):
    files: List[str]


def _resolve_search_dir(search_dir: Optional[str]) -> Path:
    """
    Resolve a user‑provided search_dir relative to BASE_DIR.
    Prevent directory‑traversal attacks by ensuring the final path is still inside BASE_DIR.
    """
    if not search_dir:
        return BASE_DIR

    # Strip leading slash to treat as relative path
    rel_path = search_dir.lstrip("/")
    candidate = (BASE_DIR / rel_path).resolve()

    if not str(candidate).startswith(str(BASE_DIR)):
        raise ValueError("search_dir resolves outside of the data directory")

    return candidate


def _file_contains_content(file_path: Path, content_bytes: bytes, read_limit: int) -> bool:
    """
    Return True if the binary content of file_path contains the given byte sequence.
    Reads the file in chunks to avoid loading large files entirely into memory.
    The function respects a per‑file read limit (read_limit) to avoid excessive I/O.
    """
    chunk_size = 8192
    overlap = len(content_bytes) - 1
    if overlap < 0:
        overlap = 0

    bytes_read = 0
    previous_chunk_tail = b""

    try:
        with file_path.open("rb") as f:
            while True:
                # Ensure we do not exceed the per‑file read limit
                remaining = read_limit - bytes_read
                if remaining <= 0:
                    return False
                to_read = min(chunk_size, remaining)
                chunk = f.read(to_read)
                if not chunk:
                    break

                bytes_read += len(chunk)

                # Combine tail from previous chunk to handle matches across boundaries
                data = previous_chunk_tail + chunk
                if content_bytes in data:
                    return True

                # Keep last `overlap` bytes for the next iteration
                previous_chunk_tail = data[-overlap:] if overlap else b""
        return False
    except Exception:
        # If any error occurs (e.g., permission), treat as non‑matching
        return False


def search_files(
    search_content: Optional[str],
    search_filename: Optional[str],
    search_dir: Optional[str],
) -> List[str]:
    """
    Walk the directory tree starting at the resolved search_dir and collect files that match
    either the filename criterion or the content criterion (OR logic). The walk respects
    depth, file‑count, and total‑bytes‑read limits to protect server resources.
    """
    start_path = _resolve_search_dir(search_dir)

    matches: List[str] = []
    files_scanned = 0
    total_bytes_read = 0

    # Prepare content bytes once
    content_bytes = search_content.encode() if search_content else None

    for root, _, files in os.walk(start_path, topdown=True, followlinks=False):
        # Compute depth relative to start_path
        rel_path = Path(root).relative_to(start_path)
        depth = len(rel_path.parts)
        if depth > MAX_SEARCH_DEPTH:
            # Prune deeper directories
            continue

        for filename in files:
            if files_scanned >= MAX_FILES_SCANNED:
                raise HTTPException(
                    status_code=400,
                    detail="Search aborted: too many files to scan",
                )
            files_scanned += 1

            file_path = Path(root) / filename

            # OR logic: check filename match first (cheaper)
            filename_match = (
                search_filename is not None and search_filename in filename
            )
            content_match = False

            if not filename_match and content_bytes is not None:
                # Estimate remaining read budget for this file
                remaining_budget = MAX_TOTAL_READ_BYTES - total_bytes_read
                if remaining_budget <= 0:
                    raise HTTPException(
                        status_code=400,
                        detail="Search aborted: total read byte limit exceeded",
                    )
                content_match = _file_contains_content(
                    file_path, content_bytes, remaining_budget
                )
                # Approximate bytes read (worst case: whole file up to remaining_budget)
                total_bytes_read += min(remaining_budget, os.path.getsize(file_path))

            if filename_match or content_match:
                matches.append(str(file_path))
                if len(matches) >= MAX_MATCHES:
                    # Stop collecting more matches but continue scanning to respect limits
                    break

        # If we already hit the match limit, stop walking further
        if len(matches) >= MAX_MATCHES:
            break

    return matches


@app.post(
    "/search",
    response_model=SearchResponse,
    responses={400: {"description": "Invalid input"}},
)
async def search_endpoint(request: SearchRequest) -> SearchResponse:
    # At least one non‑empty criterion must be supplied
    if not request.search_content and not request.search_filename:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one of search_content or search_filename",
        )

    try:
        files = search_files(
            search_content=request.search_content,
            search_filename=request.search_filename,
            search_dir=request.search_dir,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return SearchResponse(files=files)


if __name__ == "__main__":
    # Run the application on 0.0.0.0:5000 as requested
    uvicorn.run(app, host="0.0.0.0", port=5000)