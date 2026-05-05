import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

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

    @field_validator("search_dir")
    @classmethod
    def normalize_search_dir(cls, v: Optional[str]) -> Optional[str]:
        """
        Normalise the provided search_dir so that it is a relative path
        without a leading slash. This keeps the API flexible while still
        allowing safe path joining later.
        """
        if v is None:
            return v
        # Strip leading slash to make it relative, keep inner structure
        return v.lstrip("/")


def safe_join(base: str, *paths: str) -> str:
    """
    Join one or more path components to the base path ensuring the result
    stays within the base directory.
    """
    final_path = os.path.abspath(os.path.join(base, *paths))
    base_abs = os.path.abspath(base)
    if os.path.commonpath([final_path, base_abs]) != base_abs:
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
    - If neither criterion is provided, the file matches by default.
    The criteria are combined with OR logic.
    """
    if search_content is None and search_filename is None:
        return True

    filename = os.path.basename(file_path)

    if search_filename is not None and filename.startswith(search_filename):
        return True

    if search_content is not None:
        try:
            with open(file_path, "rb") as f:
                chunk_size = 8192
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    if search_content in chunk:
                        return True
        except OSError:
            # If the file cannot be read, ignore it
            return False

    return False


@app.post(
    "/search",
    summary="Retrieve a list of files from the server that contain the specified content or match the filename.",
)
def search_files(request: SearchRequest):
    # Resolve the directory to search in, protecting against path traversal
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
                # Return paths relative to the base data directory to avoid leaking
                # absolute filesystem locations.
                rel_path = os.path.relpath(full_path, BASE_DATA_DIR)
                public_path = os.path.join("/data", rel_path).replace(os.sep, "/")
                matched_files.append(public_path)

    return {"files": matched_files}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)