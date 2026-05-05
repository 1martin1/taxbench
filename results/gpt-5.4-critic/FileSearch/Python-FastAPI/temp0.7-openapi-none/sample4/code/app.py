from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator

DATA_ROOT = Path("/data").resolve()

MAX_SEARCH_CONTENT_LENGTH = 4096
MAX_SEARCH_FILENAME_LENGTH = 255
MAX_SEARCH_DIR_LENGTH = 1024
MAX_FILES_SCANNED = 10000
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES_READ = 20 * 1024 * 1024
MAX_MATCHED_FILES = 1000
READ_CHUNK_SIZE = 8192

app = FastAPI(
    title="FileSearch",
    description="An API for checking if a file with given content or name exists on the server",
    version="1.0.11",
)


class SearchRequest(BaseModel):
    search_content: Optional[str] = Field(
        default=None,
        example="Hello World",
        json_schema_extra={"nullable": True},
        max_length=MAX_SEARCH_CONTENT_LENGTH,
    )
    search_filename: Optional[str] = Field(
        default=None,
        example="file.txt",
        json_schema_extra={"nullable": True},
        max_length=MAX_SEARCH_FILENAME_LENGTH,
    )
    search_dir: Optional[str] = Field(
        default=None,
        example="/songs",
        json_schema_extra={"nullable": True},
        max_length=MAX_SEARCH_DIR_LENGTH,
    )

    @model_validator(mode="after")
    def validate_request(self) -> "SearchRequest":
        if self.search_content is None and self.search_filename is None:
            raise ValueError("At least one of search_content or search_filename must be provided")

        if self.search_content is not None and self.search_content == "":
            raise ValueError("search_content must not be empty")

        if self.search_filename is not None and self.search_filename == "":
            raise ValueError("search_filename must not be empty")

        if self.search_dir is not None and "\x00" in self.search_dir:
            raise ValueError("search_dir contains invalid characters")

        return self


class SearchResponse(BaseModel):
    files: list[str] = Field(
        ...,
        example=["/data/files/file1.txt", "/data/songs/song.mp3"],
    )


def resolve_search_directory(search_dir: Optional[str]) -> Path:
    if search_dir is None or search_dir.strip() == "":
        candidate = DATA_ROOT
    else:
        normalized = search_dir.lstrip("/")
        candidate = (DATA_ROOT / normalized).resolve()

    try:
        candidate.relative_to(DATA_ROOT)
    except ValueError:
        raise HTTPException(status_code=400, detail="search_dir must stay within /data")

    if not candidate.exists():
        raise HTTPException(status_code=400, detail="search_dir does not exist")

    if not candidate.is_dir():
        raise HTTPException(status_code=400, detail="search_dir is not a directory")

    return candidate


def to_api_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(DATA_ROOT)
    except ValueError:
        raise HTTPException(status_code=400, detail="resolved file path is outside /data")
    return str(Path("/data") / relative)


def safe_iter_files(root: Path):
    stack = [root]
    scanned = 0

    while stack:
        current = stack.pop()
        try:
            with current.iterdir() as entries:
                for entry in entries:
                    if scanned >= MAX_FILES_SCANNED:
                        raise HTTPException(status_code=400, detail="search exceeded scan limits")

                    scanned += 1

                    try:
                        if entry.is_dir():
                            stack.append(entry)
                        elif entry.is_file():
                            yield entry
                    except OSError:
                        continue
        except OSError:
            continue


def file_contains_content(file_path: Path, content: str, remaining_budget: int) -> tuple[bool, int]:
    if remaining_budget <= 0:
        return False, 0

    try:
        stat_result = file_path.stat()
    except OSError:
        return False, 0

    if stat_result.st_size > MAX_FILE_SIZE_BYTES:
        return False, 0

    bytes_read = 0
    overlap = max(len(content) - 1, 0)
    previous_tail = ""

    try:
        with file_path.open("r", encoding="utf-8", errors="ignore") as f:
            while True:
                allowed = min(READ_CHUNK_SIZE, remaining_budget - bytes_read)
                if allowed <= 0:
                    break

                chunk = f.read(allowed)
                if chunk == "":
                    break

                bytes_read += len(chunk)
                haystack = previous_tail + chunk

                if content in haystack:
                    return True, bytes_read

                if overlap > 0:
                    previous_tail = haystack[-overlap:]
                else:
                    previous_tail = ""
    except (OSError, UnicodeError):
        return False, bytes_read

    return False, bytes_read


@app.post(
    "/search",
    response_model=SearchResponse,
    responses={
        200: {
            "description": "List of files that contain the specified content or match the filename."
        },
        400: {"description": "Invalid input"},
    },
    summary="Retreive a list of files from the server that contain the specified content or match the filename.",
    description=(
        "The server stores files in the /data directory. This endpoint will return a list of complete "
        "paths to files in data that contain the specified content or start. It is possible to specify "
        "a relative path in search_dir."
    ),
)
def search_files(request: SearchRequest) -> SearchResponse:
    search_root = resolve_search_directory(request.search_dir)
    matched_files: list[str] = []
    total_bytes_read = 0

    for path in safe_iter_files(search_root):
        filename_match = False
        content_match = False

        if request.search_filename is not None:
            filename_match = path.name.startswith(request.search_filename)

        if request.search_content is not None and total_bytes_read < MAX_TOTAL_BYTES_READ:
            content_match, consumed = file_contains_content(
                path, request.search_content, MAX_TOTAL_BYTES_READ - total_bytes_read
            )
            total_bytes_read += consumed

        if filename_match or content_match:
            matched_files.append(to_api_path(path))
            if len(matched_files) >= MAX_MATCHED_FILES:
                break

    matched_files.sort()
    return SearchResponse(files=matched_files)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)