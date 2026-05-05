# полностью рабочая версия

import os
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, validator

app = FastAPI(title="FileSearch", description="An API for checking if a file with given content or name exists on the server", version="1.0.11")

BASE_DIR = Path("/data").resolve()


class SearchRequest(BaseModel):
    search_content: Optional[str] = Field(default=None, description="Content to look for inside files")
    search_filename: Optional[str] = Field(default=None, description="Filename (or prefix) to match")
    search_dir: Optional[str] = Field(default=None, description="Relative directory inside /data to limit the search")

    @validator("search_dir")
    def normalize_search_dir(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        # Strip leading slash to keep it relative
        return v.lstrip("/")


@app.post("/search", summary="Retrieve a list of files from the server that contain the specified content or match the filename.")
def search_files(payload: SearchRequest):
    if not payload.search_content and not payload.search_filename:
        return {"files": []}

    # Determine the directory to search
    target_dir = BASE_DIR
    if payload.search_dir:
        target_dir = (BASE_DIR / payload.search_dir).resolve()
        # Security check: ensure the resolved path is still under BASE_DIR
        if not str(target_dir).startswith(str(BASE_DIR)):
            raise HTTPException(status_code=400, detail="Invalid search_dir; path traversal is not allowed.")

    if not target_dir.is_dir():
        raise HTTPException(status_code=400, detail="search_dir does not exist or is not a directory.")

    matching_files: List[str] = []

    # Prepare search criteria
    filename_criteria = payload.search_filename
    content_criteria_bytes = payload.search_content.encode("utf-8") if payload.search_content else None

    for root, _, files in os.walk(target_dir, followlinks=False):
        for file_name in files:
            file_path = Path(root) / file_name
            resolved_path = file_path.resolve()
            if not str(resolved_path).startswith(str(BASE_DIR)):
                continue

            # Check filename match (simple substring match)
            filename_match = (
                filename_criteria is not None and filename_criteria in file_name
            )

            # Check content match
            content_match = False
            if content_criteria_bytes is not None:
                try:
                    with open(file_path, "rb") as f:
                        # Read in chunks to avoid loading huge files entirely
                        while chunk := f.read(8192):
                            if content_criteria_bytes in chunk:
                                content_match = True
                                break
                except (OSError, PermissionError):
                    # Skip files that cannot be read
                    continue

            # If either condition matches, add to result
            if filename_match or content_match:
                matching_files.append(str(file_path.resolve()))

    return {"files": matching_files}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)