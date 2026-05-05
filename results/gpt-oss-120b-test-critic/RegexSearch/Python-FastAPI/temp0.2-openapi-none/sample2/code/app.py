import re
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI(
    title="File Search API",
    description="API for searching files based on matching a given regex against their content.",
    version="1.0.0",
)

# Root directory where files are stored (relative to the current working directory)
FILES_ROOT = (Path.cwd() / "files").resolve()

# Security / resource limits
MAX_REGEX_LENGTH = 200          # characters
MAX_FILES_PROCESSED = 1000      # maximum files to examine per request
MAX_RECURSION_DEPTH = 10        # maximum sub‑directory depth when recursive=True
CHUNK_SIZE = 1024 * 1024        # 1 MiB – read files in chunks to avoid loading huge files entirely


def _safe_compile_regex(pattern: str) -> re.Pattern:
    """
    Compile a regex pattern safely, enforcing a maximum length to mitigate
    potential ReDoS attacks.
    """
    if len(pattern) > MAX_REGEX_LENGTH:
        raise ValueError(
            f"Regex pattern too long (max {MAX_REGEX_LENGTH} characters allowed)."
        )
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"Invalid regex: {exc}") from exc


def _search_file(path: Path, compiled_regex: re.Pattern) -> bool:
    """
    Search for the compiled regex inside the file at *path*.
    The file is read in binary chunks to keep memory usage low.
    """
    try:
        with path.open("rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                # Decode chunk as text, ignoring errors
                text = chunk.decode(errors="ignore")
                if compiled_regex.search(text):
                    return True
    except Exception:
        # If the file cannot be read for any reason, treat it as non‑matching.
        return False
    return False


@app.get("/search")
async def search(
    regex: str = Query(
        ...,
        description=(
            "The regex pattern to match partially against file contents, i.e. some part "
            "of the file content must match the regex."
        ),
    ),
    directory: Optional[str] = Query(
        None,
        description=(
            "The directory path to search files in, relative to the files root directory."
        ),
    ),
    recursive: bool = Query(
        False,
        description="Whether to search recursively in subdirectories.",
    ),
):
    """
    Search files by regex.
    Returns a JSON object with a list of file paths (relative to the searched directory)
    that match the regex.
    """
    # Compile regex safely
    try:
        pattern = _safe_compile_regex(regex)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    # Resolve the directory to search within
    if directory:
        search_path = (FILES_ROOT / directory).resolve()
    else:
        search_path = FILES_ROOT

    # Ensure the resolved path stays within FILES_ROOT
    try:
        search_path.relative_to(FILES_ROOT)
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"error": "Requested directory is outside the allowed files root."},
        )

    if not search_path.is_dir():
        return JSONResponse(
            status_code=400,
            content={"error": "Specified directory does not exist or is not a directory."},
        )

    # Choose iterator based on recursion flag
    iterator = (
        search_path.rglob("*") if recursive else search_path.glob("*")
    )

    matching_files: List[str] = []
    processed_files = 0

    for path in iterator:
        if not path.is_file():
            continue

        # Enforce recursion depth limit
        if recursive:
            relative_parts = path.relative_to(search_path).parts
            # depth = number of directories in the relative path
            depth = len(relative_parts) - 1
            if depth > MAX_RECURSION_DEPTH:
                continue

        processed_files += 1
        if processed_files > MAX_FILES_PROCESSED:
            # Stop processing further files to protect resources
            break

        if _search_file(path, pattern):
            relative_path = path.relative_to(search_path).as_posix()
            matching_files.append(relative_path)

    return JSONResponse(status_code=200, content={"files": matching_files})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)