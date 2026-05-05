import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, validator

app = FastAPI(
    title="FileSearch",
    description="An API for checking if a file with given content or name exists on the server",
    version="1.0.11",
)

BASE_DATA_DIR = "/data"


class SearchRequest(BaseModel):
    search_content: Optional[str] = Field(
        default=None, description="Content to search inside files"
    )
    search_filename: Optional[str] = Field(
        default=None, description="Filename (or prefix) to match"
    )
    search_dir: Optional[str] = Field(
        default=None,
        description="Relative directory inside /data to limit the search (e.g. /songs)",
    )

    @validator("search_dir")
    def normalize_search_dir(cls, v):
        if v is None:
            return v
        # Remove leading slash to make it relative, but keep inner structure
        return v.lstrip("/")

    @validator("*")
    def at_least_one_criteria(cls, v, values, **kwargs):
        # This validator runs for each field; we will enforce after model creation
        return v

    @validator("__root__", pre=True, always=True)
    def ensure_criteria(cls, values):
        # Ensure at least one of the optional fields is provided
        if not any(values.get(k) for k in ("search_content", "search_filename")):
            raise ValueError(
                "At least one of 'search_content' or 'search_filename' must be provided."
            )
        return values


def safe_join(base: str, *paths: str) -> str:
    """
    Join one or more path components to the base path ensuring the result
    stays within the base directory.
    """
    final_path = os.path.abspath(os.path.join(base, *paths))
    if os.path.commonpath([final_path, base]) != os.path.abspath(base):
        raise ValueError("Attempted path traversal outside of base directory.")
    return final_path


def file_matches(
    file_path: str,
    search_content: Optional[bytes],
    search_filename: Optional[str],
) -> bool:
    """
    Determine whether a file matches the given criteria.
    - If search_filename is set, the file's name must start with that string.
    - If search_content is set, the file must contain the byte sequence.
    Both criteria are OR-ed (match any).
    """
    filename = os.path.basename(file_path)

    if search_filename is not None and filename.startswith(search_filename):
        return True

    if search_content is not None:
        try:
            with open(file_path, "rb") as f:
                # Read in chunks to avoid loading huge files into memory
                chunk_size = 8192
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    if search_content in chunk:
                        return True
        except (OSError, UnicodeDecodeError):
            # If the file cannot be read, just ignore it
            return False

    return False


@app.post("/search", summary="Retrieve a list of files from the server that contain the specified content or match the filename.")
def search_files(request: SearchRequest):
    # Determine the root directory for the search
    try:
        if request.search_dir:
            root_dir = safe_join(BASE_DATA_DIR, request.search_dir)
        else:
            root_dir = BASE_DATA_DIR
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not os.path.isdir(root_dir):
        raise HTTPException(status_code=400, detail="Specified search_dir does not exist.")

    # Prepare search criteria
    search_content_bytes = (
        request.search_content.encode("utf-8") if request.search_content else None
    )
    search_filename = request.search_filename

    matched_files: List[str] = []

    for dirpath, _, filenames in os.walk(root_dir):
        for name in filenames:
            full_path = os.path.join(dirpath, name)
            if file_matches(full_path, search_content_bytes, search_filename):
                matched_files.append(full_path)

    return {"files": matched_files}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)