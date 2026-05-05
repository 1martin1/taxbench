import os
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

app = FastAPI(
    title="FileSearch",
    version="1.0.11",
    description="An API for checking if a file with given content or name exists on the server",
)

BASE_DATA_DIR = Path("/data").resolve()
# Ensure the base directory exists so the service can start even if it was not pre‑created.
BASE_DATA_DIR.mkdir(parents=True, exist_ok=True)


class SearchRequest(BaseModel):
    search_content: Optional[str] = Field(
        default=None, description="Content to search inside files"
    )
    search_filename: Optional[str] = Field(
        default=None, description="Exact filename to match"
    )
    search_dir: Optional[str] = Field(
        default=None,
        description="Relative directory inside /data to limit the search",
    )

    @field_validator("search_dir")
    @classmethod
    def dir_must_be_relative(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        p = Path(v)
        # Disallow absolute paths
        if p.is_absolute():
            raise ValueError("search_dir must be a relative path")
        # Disallow parent directory traversal components
        if ".." in p.parts:
            raise ValueError("search_dir must not contain parent directory references")
        return v


def safe_join(base: Path, *paths: str) -> Path:
    """
    Join one or more path components to the base path ensuring the result stays within base.
    Symlinks are resolved and the final path must be a descendant of ``base``.
    """
    new_path = base.joinpath(*paths).resolve()
    try:
        # Python 3.9+: Path.is_relative_to
        if not new_path.is_relative_to(base):
            raise ValueError("Resulting path is outside of the allowed directory")
    except AttributeError:
        # Fallback for older Python versions
        if base != new_path and base not in new_path.parents:
            raise ValueError("Resulting path is outside of the allowed directory")
    return new_path


def file_matches(
    file_path: Path, content: Optional[str], filename: Optional[str]
) -> bool:
    """
    Return True if the file matches either the content or the filename criteria.
    """
    if filename is not None and file_path.name == filename:
        return True

    if content is not None:
        try:
            content_bytes = content.encode("utf-8")
            with file_path.open("rb") as f:
                while chunk := f.read(8192):
                    if content_bytes in chunk:
                        return True
        except Exception:
            # If the file cannot be read, skip content matching.
            pass

    return False


@app.post(
    "/search",
    summary="Retrieve a list of files from the server that contain the specified content or match the filename.",
)
def search_files(request: SearchRequest):
    # Determine the directory to search.
    try:
        search_root = BASE_DATA_DIR
        if request.search_dir:
            search_root = safe_join(BASE_DATA_DIR, request.search_dir)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    # If the target directory does not exist or is not a directory, return an empty list.
    if not search_root.is_dir():
        return {"files": []}

    matched_files: List[str] = []

    for path in search_root.rglob("*"):
        if path.is_file():
            if file_matches(path, request.search_content, request.search_filename):
                # Return paths relative to the base data directory to avoid exposing absolute server paths.
                relative_path = path.relative_to(BASE_DATA_DIR).as_posix()
                matched_files.append(relative_path)

    return {"files": matched_files}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)