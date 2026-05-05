from pathlib import Path
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

DATA_ROOT = Path("/data").resolve()

MAX_SEARCH_CONTENT_LENGTH = 4096
MAX_SEARCH_FILENAME_LENGTH = 255
MAX_SEARCH_DIR_LENGTH = 1024
MAX_FILES_SCANNED = 10000
MAX_MATCHED_FILES = 1000
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024


app = FastAPI(
    title="FileSearch",
    description="An API for checking if a file with given content or name exists on the server",
    version="1.0.11",
)


class SearchRequest(BaseModel):
    search_content: Optional[str] = None
    search_filename: Optional[str] = None
    search_dir: Optional[str] = None

    class Config:
        extra = "forbid"


class SearchResponse(BaseModel):
    files: List[str]


def normalize_optional_string(value: Optional[str], strip_whitespace: bool = False) -> Optional[str]:
    if value is None:
        return None
    if strip_whitespace:
        value = value.strip()
    if value == "":
        return None
    return value


def validate_request(request: SearchRequest) -> SearchRequest:
    request.search_content = normalize_optional_string(request.search_content)
    request.search_filename = normalize_optional_string(request.search_filename)
    request.search_dir = normalize_optional_string(request.search_dir, strip_whitespace=True)

    if request.search_content is None and request.search_filename is None:
        raise HTTPException(
            status_code=400,
            detail="At least one of search_content or search_filename must be provided",
        )

    if request.search_content is not None and len(request.search_content) > MAX_SEARCH_CONTENT_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid input")

    if request.search_filename is not None and len(request.search_filename) > MAX_SEARCH_FILENAME_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid input")

    if request.search_dir is not None and len(request.search_dir) > MAX_SEARCH_DIR_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid input")

    return request


def resolve_search_directory(search_dir: Optional[str]) -> Path:
    if search_dir is None:
        target = DATA_ROOT
    else:
        normalized = search_dir.lstrip("/")
        target = (DATA_ROOT / normalized).resolve()

    try:
        target.relative_to(DATA_ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid input") from exc

    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=400, detail="Invalid input")

    return target


def file_matches(path: Path, search_content: Optional[str], search_filename: Optional[str]) -> bool:
    if search_filename is not None and path.name.startswith(search_filename):
        return True

    if search_content is None:
        return False

    try:
        stat_result = path.stat()
        if stat_result.st_size > MAX_FILE_SIZE_BYTES:
            return False
    except OSError:
        return False

    try:
        with path.open("r", encoding="utf-8", errors="ignore") as file_handle:
            for line in file_handle:
                if search_content in line:
                    return True
    except (OSError, UnicodeError):
        return False

    return False


@app.post("/search", response_model=SearchResponse)
def search_files(request: SearchRequest) -> SearchResponse:
    request = validate_request(request)
    search_root = resolve_search_directory(request.search_dir)
    matched_files: List[str] = []
    scanned_files = 0

    try:
        iterator = search_root.rglob("*")
        for path in iterator:
            if len(matched_files) >= MAX_MATCHED_FILES:
                break

            try:
                if not path.is_file():
                    continue
            except OSError:
                continue

            scanned_files += 1
            if scanned_files > MAX_FILES_SCANNED:
                break

            if file_matches(path, request.search_content, request.search_filename):
                try:
                    resolved = path.resolve()
                    resolved.relative_to(DATA_ROOT)
                    matched_files.append(str(resolved))
                except (OSError, ValueError):
                    continue
    except OSError:
        raise HTTPException(status_code=400, detail="Invalid input")

    matched_files.sort()
    return SearchResponse(files=matched_files)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)