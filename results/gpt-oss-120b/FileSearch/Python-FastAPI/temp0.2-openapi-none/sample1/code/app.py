from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List
from pathlib import Path

app = FastAPI(title="FileSearch", description="An API for checking if a file with given content or name exists on the server", version="1.0.11")

BASE_DIR = Path("/data").resolve()


class SearchRequest(BaseModel):
    search_content: Optional[str] = Field(default=None, description="Content to look for inside files")
    search_filename: Optional[str] = Field(default=None, description="Filename prefix to match")
    search_dir: Optional[str] = Field(default=None, description="Relative directory inside /data to limit the search")


class SearchResponse(BaseModel):
    files: List[str] = Field(..., description="List of absolute file paths that match the criteria")


def _safe_target_dir(search_dir: Optional[str]) -> Path:
    """
    Resolve the target directory safely, ensuring it stays inside BASE_DIR.
    """
    target = BASE_DIR
    if search_dir:
        # Strip leading slash to treat it as relative, then resolve
        rel_path = Path(search_dir.lstrip("/"))
        target = (BASE_DIR / rel_path).resolve()
        # Security check: the resolved path must be within BASE_DIR
        if not str(target).startswith(str(BASE_DIR)):
            raise HTTPException(status_code=400, detail="Invalid search_dir; path traversal is not allowed.")
    return target


def _file_matches(file_path: Path, filename_prefix: Optional[str], content_bytes: Optional[bytes]) -> bool:
    """
    Determine whether a file matches either the filename prefix or contains the content bytes.
    """
    if filename_prefix and file_path.name.startswith(filename_prefix):
        return True

    if content_bytes:
        try:
            data = file_path.read_bytes()
            if content_bytes in data:
                return True
        except Exception:
            # If the file cannot be read (e.g., permission issues), ignore it.
            pass

    return False


@app.post("/search", response_model=SearchResponse, responses={400: {"description": "Invalid input"}})
def search_files(request: SearchRequest) -> SearchResponse:
    if not any([request.search_content, request.search_filename, request.search_dir]):
        raise HTTPException(status_code=400, detail="At least one of search_content, search_filename, or search_dir must be provided.")

    target_dir = _safe_target_dir(request.search_dir)

    content_bytes = request.search_content.encode() if request.search_content else None
    filename_prefix = request.search_filename

    matched_files: List[str] = []

    for path in target_dir.rglob("*"):
        if not path.is_file():
            continue
        if _file_matches(path, filename_prefix, content_bytes):
            matched_files.append(str(path.resolve()))

    return SearchResponse(files=matched_files)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)