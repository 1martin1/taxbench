import os
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

app = FastAPI(
    title="FileSearch",
    description="An API for checking if a file with given content or name exists on the server",
    version="1.0.11",
)

BASE_DIR = Path("/data").resolve()


class SearchRequest(BaseModel):
    search_content: Optional[str] = Field(
        default=None, description="Content to look for inside files"
    )
    search_filename: Optional[str] = Field(
        default=None, description="Filename (or prefix) to match"
    )
    search_dir: Optional[str] = Field(
        default=None,
        description="Relative directory inside /data to limit the search",
    )

    @field_validator("search_dir")
    @classmethod
    def normalize_search_dir(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        # Strip leading slash to keep it relative and remove any trailing slashes
        return v.lstrip("/").rstrip("/")


@app.post(
    "/search",
    summary="Retrieve a list of files from the server that contain the specified content or match the filename.",
)
def search_files(payload: SearchRequest):
    # At least one search criterion must be provided
    if not payload.search_content and not payload.search_filename:
        raise HTTPException(
            status_code=400,
            detail="At least one of 'search_content' or 'search_filename' must be provided.",
        )

    # Resolve the directory to search
    target_dir = BASE_DIR
    if payload.search_dir:
        target_dir = (BASE_DIR / payload.search_dir).resolve()
        # Ensure the resolved path is still under BASE_DIR (prevents path traversal)
        try:
            target_dir.relative_to(BASE_DIR)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid search_dir; path traversal is not allowed.",
            )

    # If the target directory does not exist, return an empty list per specification
    if not target_dir.is_dir():
        return {"files": []}

    matching_files: List[str] = []

    filename_criteria = payload.search_filename
    content_criteria = payload.search_content
    content_bytes = content_criteria.encode("utf-8") if content_criteria else None

    for root, _, files in os.walk(target_dir):
        for file_name in files:
            file_path = Path(root) / file_name

            # Filename match (simple substring)
            filename_match = (
                filename_criteria is not None and filename_criteria in file_name
            )

            # Content match
            content_match = False
            if content_bytes is not None:
                try:
                    with open(file_path, "rb") as f:
                        # Read the whole file to avoid missing matches across chunk boundaries
                        data = f.read()
                        if content_bytes in data:
                            content_match = True
                except (OSError, PermissionError):
                    # Skip unreadable files
                    continue

            if filename_match or content_match:
                # Return paths relative to BASE_DIR prefixed with /data to avoid leaking full system paths
                try:
                    rel_path = file_path.resolve().relative_to(BASE_DIR)
                    display_path = f"/data/{rel_path.as_posix()}"
                except ValueError:
                    # Fallback to absolute path if something unexpected occurs
                    display_path = str(file_path.resolve())
                matching_files.append(display_path)

    return {"files": matching_files}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)