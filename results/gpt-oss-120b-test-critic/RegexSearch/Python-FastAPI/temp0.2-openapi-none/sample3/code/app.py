import os
import re
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

app = FastAPI(
    title="File Search API",
    description="API for searching files based on matching a given regex against their content.",
    version="1.0.0",
)

# Root directory where files are stored (resolved absolute path)
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
        raise HTTPException(
            status_code=400,
            detail="Requested directory is outside the allowed root.",
        )
    if not candidate.is_dir():
        raise HTTPException(
            status_code=400,
            detail="Requested directory does not exist or is not a directory.",
        )
    return candidate


def _iter_files(base: Path, recursive: bool):
    """
    Yield file paths under `base` without following symlinks.
    All yielded paths are guaranteed to stay within ROOT_DIR.
    """
    if recursive:
        for root, dirs, files in os.walk(base, followlinks=False):
            # Remove symlink directories from traversal
            dirs[:] = [
                d
                for d in dirs
                if not Path(os.path.join(root, d)).is_symlink()
            ]
            for f in files:
                file_path = Path(root) / f
                if file_path.is_symlink():
                    continue
                try:
                    file_path.resolve().relative_to(ROOT_DIR)
                except ValueError:
                    # Path escaped the root – skip it
                    continue
                yield file_path
    else:
        for entry in base.iterdir():
            if entry.is_file() and not entry.is_symlink():
                try:
                    entry.resolve().relative_to(ROOT_DIR)
                except ValueError:
                    continue
                yield entry


MAX_REGEX_LENGTH = 200  # Prevent excessively long patterns


@app.get(
    "/search",
    response_model=SearchResult,
    responses={
        400: {"description": "Invalid input, such as malformed regex or inaccessible directory."},
        500: {"description": "Internal server error."},
    },
)
def search(
    regex: str = Query(..., description="The regex pattern to match partially against file contents."),
    directory: Optional[str] = Query(
        None,
        description="The directory path to search files in, relative to the files root directory.",
    ),
    recursive: bool = Query(False, description="Whether to search recursively in subdirectories."),
):
    # Basic length validation to mitigate ReDoS attacks
    if len(regex) > MAX_REGEX_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Regex pattern is too long (maximum {MAX_REGEX_LENGTH} characters).",
        )

    # Compile regex safely
    try:
        pattern = re.compile(regex)
    except re.error:
        raise HTTPException(
            status_code=400,
            detail="Invalid regex pattern.",
        )

    # Resolve and validate the target directory
    target_dir = _safe_path(directory)

    matched_files: List[str] = []

    try:
        for path in _iter_files(target_dir, recursive):
            try:
                # Read file content as text, ignore decoding errors
                content = path.read_text(errors="ignore")
            except Exception:
                # Skip unreadable files without exposing the error
                continue

            if pattern.search(content):
                rel_path = path.relative_to(target_dir)
                matched_files.append(str(rel_path).replace("\\", "/"))
    except Exception:
        # Generic internal error without leaking details
        raise HTTPException(
            status_code=500,
            detail="Internal server error while searching files.",
        )

    return SearchResult(files=matched_files)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)