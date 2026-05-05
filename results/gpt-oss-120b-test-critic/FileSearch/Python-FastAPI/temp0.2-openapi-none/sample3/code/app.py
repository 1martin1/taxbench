import os
from typing import List, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(
    title="FileSearch",
    version="1.0.11",
    description="An API for checking if a file with given content or name exists on the server",
)

# Base directory where files are stored. It may or may not exist at runtime.
BASE_DATA_DIR = "/data"


class SearchRequest(BaseModel):
    search_content: Optional[str] = Field(
        default=None, description="Content to look for inside files"
    )
    search_filename: Optional[str] = Field(
        default=None, description="Filename (or part of it) to match"
    )
    search_dir: Optional[str] = Field(
        default=None,
        description="Relative directory inside /data to limit the search",
    )


class SearchResponse(BaseModel):
    files: List[str]


def _is_content_in_file(file_path: str, content_bytes: bytes) -> bool:
    """
    Efficiently check whether ``content_bytes`` appears in ``file_path``.
    Reads the file in chunks to avoid loading large files entirely into memory.
    """
    chunk_size = 8192
    try:
        with open(file_path, "rb") as f:
            overlap = b""
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                data = overlap + chunk
                if content_bytes in data:
                    return True
                # Keep the last len(content_bytes)-1 bytes for the next iteration
                if len(content_bytes) > 1:
                    overlap = data[-(len(content_bytes) - 1) :]
                else:
                    overlap = b""
    except (OSError, PermissionError):
        # If the file cannot be read, treat it as non‑matching.
        return False
    return False


@app.post(
    "/search",
    response_model=SearchResponse,
    responses={400: {"description": "Invalid input"}},
)
def search_files(request: SearchRequest) -> SearchResponse:
    """
    Search for files under ``BASE_DATA_DIR`` (optionally limited to a sub‑directory)
    that either contain the specified content or whose filename matches the given pattern.
    The function never raises a 400 error for missing directories; it simply returns an
    empty list, complying with the OpenAPI contract.
    """
    # Resolve the directory to search.
    base_dir_abs = os.path.abspath(BASE_DATA_DIR)

    if request.search_dir:
        # Treat the provided path as relative to BASE_DATA_DIR.
        rel_path = request.search_dir.lstrip("/")
        target_dir = os.path.abspath(os.path.join(base_dir_abs, rel_path))
        # Prevent path traversal outside the base directory.
        if not target_dir.startswith(base_dir_abs + os.sep):
            # Return empty result instead of error to stay within spec.
            return SearchResponse(files=[])
    else:
        target_dir = base_dir_abs

    # If the target directory does not exist or is not a directory, return empty result.
    if not os.path.isdir(target_dir):
        return SearchResponse(files=[])

    # Prepare search parameters.
    content_bytes = request.search_content.encode("utf-8") if request.search_content else None
    filename_substr = request.search_filename.lower() if request.search_filename else None

    # If no criteria are supplied, the spec does not require any matches.
    if not content_bytes and not filename_substr:
        return SearchResponse(files=[])

    matched_files: List[str] = []

    for root, _, files in os.walk(target_dir):
        for fname in files:
            full_path = os.path.join(root, fname)

            # Filename match (case‑insensitive) takes precedence.
            if filename_substr and filename_substr in fname.lower():
                matched_files.append(full_path)
                continue

            # Content match.
            if content_bytes and _is_content_in_file(full_path, content_bytes):
                matched_files.append(full_path)

    return SearchResponse(files=matched_files)


if __name__ == "__main__":
    import os

    import uvicorn

    # Allow the port to be overridden via the PORT environment variable.
    port = int(os.getenv("PORT", "5000"))
    uvicorn.run(app, host="0.0.0.0", port=port)