import os
from pathlib import Path
from typing import List, Optional, Union

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, validator

app = FastAPI(title="FileSearch", version="1.0.11", description="An API for checking if a file with given content or name exists on the server")


class SearchRequest(BaseModel):
    search_content: Optional[Union[str, None]] = Field(default=None, description="Content to search inside files")
    search_filename: Optional[Union[str, None]] = Field(default=None, description="Exact filename to match")
    search_dir: Optional[Union[str, None]] = Field(default=None, description="Relative directory inside /data to limit the search")

    @validator("search_dir")
    def dir_must_be_relative(cls, v):
        if v is None:
            return v
        # Disallow absolute paths and parent directory traversal
        if Path(v).is_absolute():
            raise ValueError("search_dir must be a relative path")
        if ".." in Path(v).parts:
            raise ValueError("search_dir must not contain parent directory references")
        return v


BASE_DATA_DIR = Path("/data").resolve()


def safe_join(base: Path, *paths: Union[str, Path]) -> Path:
    """
    Join one or more path components to the base path ensuring the result stays within base.
    """
    new_path = base.joinpath(*paths).resolve()
    if not str(new_path).startswith(str(base)):
        raise ValueError("Resulting path is outside of the allowed directory")
    return new_path


def file_matches(file_path: Path, content: Optional[str], filename: Optional[str]) -> bool:
    """
    Return True if the file matches either the content or the filename criteria.
    """
    if filename is not None and file_path.name == filename:
        return True

    if content is not None:
        try:
            # Read as binary and search for the UTF-8 encoded content
            content_bytes = content.encode("utf-8")
            with file_path.open("rb") as f:
                # Read in chunks to avoid loading huge files entirely
                while chunk := f.read(8192):
                    if content_bytes in chunk:
                        return True
        except Exception:
            # If we cannot read the file, just skip content matching
            pass

    return False


@app.post("/search", summary="Retrieve a list of files from the server that contain the specified content or match the filename.")
def search_files(request: SearchRequest):
    # Determine the directory to search
    try:
        search_root = BASE_DATA_DIR
        if request.search_dir:
            search_root = safe_join(BASE_DATA_DIR, request.search_dir)
        if not search_root.is_dir():
            raise HTTPException(status_code=400, detail="search_dir does not exist or is not a directory")
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    matched_files: List[str] = []

    # Walk through the directory recursively
    for path in search_root.rglob("*"):
        if path.is_file():
            if file_matches(path, request.search_content, request.search_filename):
                matched_files.append(str(path))

    return {"files": matched_files}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)