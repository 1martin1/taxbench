from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

DATA_DIR = Path("/data").resolve()

MAX_SEARCH_CONTENT_LENGTH = 4096
MAX_SEARCH_FILENAME_LENGTH = 255
MAX_SEARCH_DIR_LENGTH = 4096
MAX_RESULTS = 1000
MAX_FILES_SCANNED = 10000
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024
READ_CHUNK_SIZE = 8192


app = FastAPI(
    title="FileSearch",
    description="An API for checking if a file with given content or name exists on the server",
    version="1.0.11",
)


class SearchRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "type": "object",
            "properties": {
                "search_content": {
                    "type": "string",
                    "nullable": True,
                    "example": "Hello World",
                },
                "search_filename": {
                    "type": "string",
                    "nullable": True,
                    "example": "file.txt",
                },
                "search_dir": {
                    "type": "string",
                    "nullable": True,
                    "example": "/songs",
                },
            },
        },
    )

    search_content: Optional[str] = Field(default=None, max_length=MAX_SEARCH_CONTENT_LENGTH)
    search_filename: Optional[str] = Field(default=None, max_length=MAX_SEARCH_FILENAME_LENGTH)
    search_dir: Optional[str] = Field(default=None, max_length=MAX_SEARCH_DIR_LENGTH)

    @field_validator("search_dir")
    @classmethod
    def validate_search_dir(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if not isinstance(value, str):
            raise ValueError("search_dir must be a string or null")
        return value

    @field_validator("search_content", "search_filename")
    @classmethod
    def validate_search_fields(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if not isinstance(value, str):
            raise ValueError("must be a string or null")
        return value


class SearchResponse(BaseModel):
    files: list[str]


def resolve_search_root(search_dir: Optional[str]) -> Path:
    if search_dir is None or search_dir.strip() == "":
        candidate = DATA_DIR
    else:
        raw = search_dir.strip()
        if raw.startswith("/"):
            raw = raw[1:]
        candidate = (DATA_DIR / raw).resolve(strict=False)

    if not candidate.exists():
        raise HTTPException(status_code=400, detail="search_dir does not exist")
    if not candidate.is_dir():
        raise HTTPException(status_code=400, detail="search_dir is not a directory")

    return candidate


def is_within_data_dir(path: Path) -> bool:
    try:
        path.resolve().relative_to(DATA_DIR)
        return True
    except (ValueError, OSError):
        return False


def iter_files_limited(root: Path):
    scanned = 0
    stack = [root]

    while stack:
        current = stack.pop()
        try:
            with current.iterdir() as entries:
                for entry in entries:
                    if scanned >= MAX_FILES_SCANNED:
                        raise HTTPException(
                            status_code=400,
                            detail="Search exceeds allowed scan limit",
                        )

                    scanned += 1

                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir():
                            if is_within_data_dir(entry):
                                stack.append(entry)
                            continue
                        if entry.is_file() and is_within_data_dir(entry):
                            yield entry
                    except OSError:
                        continue
        except OSError:
            continue


def file_matches_content(file_path: Path, search_content: str) -> bool:
    try:
        if file_path.is_symlink():
            return False

        resolved = file_path.resolve()
        if not is_within_data_dir(resolved):
            return False

        stat_result = resolved.stat()
        if stat_result.st_size > MAX_FILE_SIZE_BYTES:
            return False

        needle = search_content.encode("utf-8")
        overlap = max(len(needle) - 1, 0)
        previous_tail = b""

        with resolved.open("rb") as f:
            while True:
                chunk = f.read(READ_CHUNK_SIZE)
                if not chunk:
                    break

                data = previous_tail + chunk
                if needle in data:
                    return True

                if overlap > 0:
                    previous_tail = data[-overlap:]
                else:
                    previous_tail = b""
    except (OSError, UnicodeError, ValueError):
        return False

    return False


@app.post("/search", response_model=SearchResponse)
def search_files(request: SearchRequest) -> SearchResponse:
    if request.search_content is None and request.search_filename is None:
        raise HTTPException(
            status_code=400,
            detail="At least one of search_content or search_filename must be provided",
        )

    search_root = resolve_search_root(request.search_dir)
    matched_files: list[str] = []

    for path in iter_files_limited(search_root):
        filename_match = False
        content_match = False

        if request.search_filename is not None:
            filename_match = path.name.startswith(request.search_filename)

        if request.search_content is not None:
            content_match = file_matches_content(path, request.search_content)

        if filename_match or content_match:
            try:
                matched_files.append(str(path.resolve()))
            except OSError:
                continue

            if len(matched_files) >= MAX_RESULTS:
                break

    return SearchResponse(files=matched_files)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)