import re
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

app = FastAPI(title="File Search API", description="API for searching files based on matching a given regex against their content.", version="1.0.0")

# Root directory where files are stored
ROOT_DIR = (Path(__file__).parent / "files").resolve()


class SearchResult(BaseModel):
    files: List[str]


def _safe_path(sub_path: Optional[str]) -> Path:
    """
    Resolve a subdirectory path safely under ROOT_DIR.
    Raises HTTPException(400) if the path is invalid or escapes ROOT_DIR.
    """
    if not sub_path:
        return ROOT_DIR

    candidate = (ROOT_DIR / sub_path).resolve()
    try:
        candidate.relative_to(ROOT_DIR)
    except ValueError:
        raise HTTPException(status_code=400, detail="Requested directory is outside the allowed root.")
    if not candidate.is_dir():
        raise HTTPException(status_code=400, detail="Requested directory does not exist or is not a directory.")
    return candidate


@app.get("/search", response_model=SearchResult, responses={
    400: {"description": "Invalid input, such as malformed regex or inaccessible directory."},
    500: {"description": "Internal server error."}
})
def search(
    regex: str = Query(..., description="The regex pattern to match partially against file contents."),
    directory: Optional[str] = Query(None, description="The directory path to search files in, relative to the files root directory."),
    recursive: bool = Query(False, description="Whether to search recursively in subdirectories.")
):
    # Compile regex, return 400 on error
    try:
        pattern = re.compile(regex)
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid regex pattern: {exc}")

    # Resolve and validate the target directory
    try:
        target_dir = _safe_path(directory)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Error processing directory: {exc}")

    # Choose glob pattern based on recursion flag
    glob_pattern = "**/*" if recursive else "*"

    matched_files: List[str] = []

    try:
        for path in target_dir.glob(glob_pattern):
            if not path.is_file():
                continue
            try:
                # Read file content as text, ignore decoding errors
                content = path.read_text(errors="ignore")
            except Exception:
                # Skip files that cannot be read
                continue

            if pattern.search(content):
                # Relative path to the provided directory (or root if none)
                rel_path = path.relative_to(target_dir)
                # Ensure POSIX style separators for consistency
                matched_files.append(str(rel_path).replace("\\", "/"))
    except Exception as exc:
        # Unexpected errors while scanning files
        raise HTTPException(status_code=500, detail=f"Server error while searching files: {exc}")

    return SearchResult(files=matched_files)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)