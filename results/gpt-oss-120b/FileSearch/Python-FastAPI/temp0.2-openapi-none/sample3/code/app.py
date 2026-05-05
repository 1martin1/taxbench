import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="FileSearch", version="1.0.11", description="An API for checking if a file with given content or name exists on the server")


BASE_DATA_DIR = "/data"


class SearchRequest(BaseModel):
    search_content: Optional[str] = Field(default=None, description="Content to look for inside files")
    search_filename: Optional[str] = Field(default=None, description="Filename (or part of it) to match")
    search_dir: Optional[str] = Field(default=None, description="Relative directory inside /data to limit the search")


class SearchResponse(BaseModel):
    files: List[str]


def _is_content_in_file(file_path: str, content_bytes: bytes) -> bool:
    """Check if the given byte sequence appears in the file."""
    # Read in chunks to avoid loading huge files into memory
    chunk_size = 8192
    try:
        with open(file_path, "rb") as f:
            overlap = b""
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                # prepend overlap from previous chunk
                data = overlap + chunk
                if content_bytes in data:
                    return True
                # keep last len(content_bytes)-1 bytes as overlap for next iteration
                if len(content_bytes) > 1:
                    overlap = data[-(len(content_bytes) - 1) :]
                else:
                    overlap = b""
    except (OSError, PermissionError):
        # If we cannot read the file, just treat it as non‑matching
        return False
    return False


@app.post("/search", response_model=SearchResponse, responses={400: {"description": "Invalid input"}})
def search_files(request: SearchRequest):
    # Validate that at least one search criterion is provided
    if not request.search_content and not request.search_filename:
        raise HTTPException(status_code=400, detail="At least one of 'search_content' or 'search_filename' must be provided.")

    # Resolve the directory to search
    target_dir = BASE_DATA_DIR
    if request.search_dir:
        # Normalise the user‑provided path and ensure it stays inside BASE_DATA_DIR
        # Strip leading slashes to treat it as relative
        rel_path = request.search_dir.lstrip("/")
        target_dir = os.path.abspath(os.path.join(BASE_DATA_DIR, rel_path))
        if not target_dir.startswith(os.path.abspath(BASE_DATA_DIR) + os.sep):
            raise HTTPException(status_code=400, detail="search_dir must be inside the data directory.")
    else:
        target_dir = os.path.abspath(BASE_DATA_DIR)

    if not os.path.isdir(target_dir):
        raise HTTPException(status_code=400, detail="search_dir does not exist or is not a directory.")

    matched_files: List[str] = []

    # Prepare search parameters
    content_bytes = request.search_content.encode("utf-8") if request.search_content else None
    filename_substr = request.search_filename.lower() if request.search_filename else None

    for root, _, files in os.walk(target_dir):
        for fname in files:
            full_path = os.path.join(root, fname)

            # Check filename match first (if requested)
            if filename_substr and filename_substr in fname.lower():
                matched_files.append(full_path)
                continue  # No need to check content if filename already matches

            # Check content match (if requested)
            if content_bytes and _is_content_in_file(full_path, content_bytes):
                matched_files.append(full_path)

    # Return paths relative to the filesystem root (as absolute paths)
    return SearchResponse(files=matched_files)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)