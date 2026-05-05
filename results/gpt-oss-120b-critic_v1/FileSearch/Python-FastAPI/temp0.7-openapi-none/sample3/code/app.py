import os
import time
from pathlib import Path
from typing import List, Optional, Dict

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Depends
from pydantic import BaseModel, Field, validator, model_validator

app = FastAPI(
    title="FileSearch",
    description="An API for checking if a file with given content or name exists on the server",
    version="1.0.11",
)

# Base directory where files are stored
BASE_DATA_DIR = Path("/data").resolve()

# Security / resource limits
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB per file
MAX_FILES_TO_PROCESS = 10_000     # maximum number of files examined per request
RATE_LIMIT = 5                   # max requests
RATE_PERIOD = 60                 # per seconds

# In‑memory store for simple rate limiting
_client_request_log: Dict[str, List[float]] = {}


def rate_limiter(request: Request):
    """Simple in‑memory rate limiter per client IP."""
    client_ip = request.client.host if request.client else "anonymous"
    now = time.time()
    timestamps = _client_request_log.get(client_ip, [])
    # Keep only timestamps within the period
    timestamps = [ts for ts in timestamps if now - ts < RATE_PERIOD]
    if len(timestamps) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many requests")
    timestamps.append(now)
    _client_request_log[client_ip] = timestamps


class SearchRequest(BaseModel):
    search_content: Optional[str] = Field(
        default=None, description="Content to look for inside files"
    )
    search_filename: Optional[str] = Field(
        default=None, description="Exact filename to match"
    )
    search_dir: Optional[str] = Field(
        default=None,
        description="Relative directory inside /data to limit the search",
    )

    @validator("search_dir")
    def normalize_dir(cls, v: Optional[str]) -> Optional[str]:
        """Strip leading slash and reject empty strings."""
        if v is None:
            return v
        norm = v.lstrip("/")
        if norm == "":
            raise ValueError("search_dir cannot be empty")
        return norm

    @model_validator(mode="after")
    def check_criteria(self):
        """Require at least one of search_content or search_filename."""
        if not (self.search_content or self.search_filename):
            raise ValueError(
                "At least one of 'search_content' or 'search_filename' must be provided."
            )
        return self


class SearchResponse(BaseModel):
    files: List[str] = Field(
        default_factory=list,
        description="List of absolute file paths that match the query",
    )


def safe_resolve(path: Path) -> Path:
    """
    Resolve a path and ensure it stays within BASE_DATA_DIR.
    """
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        raise HTTPException(status_code=400, detail="Invalid path resolution.")
    try:
        resolved.relative_to(BASE_DATA_DIR)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="search_dir must be inside the /data directory.",
        )
    return resolved


@app.post(
    "/search",
    response_model=SearchResponse,
    responses={400: {"description": "Invalid input"}, 429: {"description": "Too many requests"}},
)
def search_files(
    request: SearchRequest,
    _: None = Depends(rate_limiter),  # rate limiting dependency
) -> SearchResponse:
    # Determine the root directory for the search
    if request.search_dir:
        search_root = safe_resolve(BASE_DATA_DIR / request.search_dir)
    else:
        search_root = BASE_DATA_DIR

    if not search_root.is_dir():
        raise HTTPException(
            status_code=400, detail="search_dir does not exist or is not a directory."
        )

    matched_files: List[str] = []
    processed_files = 0
    content_bytes: Optional[bytes] = (
        request.search_content.encode("utf-8") if request.search_content else None
    )

    for root, _, files in os.walk(search_root):
        for filename in files:
            if processed_files >= MAX_FILES_TO_PROCESS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Too many files to process (limit {MAX_FILES_TO_PROCESS}).",
                )
            processed_files += 1

            file_path = Path(root) / filename

            # Filename match (exact)
            if request.search_filename and filename == request.search_filename:
                matched_files.append(str(file_path))
                continue  # No need to check content

            # Content match
            if content_bytes:
                try:
                    # Skip very large files
                    if file_path.stat().st_size > MAX_FILE_SIZE:
                        continue
                    with open(file_path, "rb") as f:
                        # Read up to MAX_FILE_SIZE bytes
                        data = f.read(MAX_FILE_SIZE)
                        if content_bytes in data:
                            matched_files.append(str(file_path))
                except (OSError, PermissionError):
                    # Skip files that cannot be accessed
                    continue

    return SearchResponse(files=matched_files)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)