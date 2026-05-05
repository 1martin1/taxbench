import os
import time
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

DATA_DIR = Path("/data").resolve()

# Resource limits to reduce abuse potential while preserving API behavior.
MAX_SEARCH_DIR_LENGTH = 1024
MAX_SEARCH_CONTENT_LENGTH = 4096
MAX_SEARCH_FILENAME_LENGTH = 255
MAX_FILES_SCANNED = 10000
MAX_MATCHES = 1000
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES_READ = 50 * 1024 * 1024
MAX_SEARCH_DURATION_SECONDS = 10.0


app = FastAPI(
    title="FileSearch",
    description="An API for checking if a file with given content or name exists on the server",
    version="1.0.11",
)


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_content: Optional[str] = Field(default=None, examples=["Hello World"])
    search_filename: Optional[str] = Field(default=None, examples=["file.txt"])
    search_dir: Optional[str] = Field(default=None, examples=["/songs"])

    @field_validator("search_content")
    @classmethod
    def validate_search_content(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if value == "":
            raise ValueError("search_content must not be empty.")
        if len(value) > MAX_SEARCH_CONTENT_LENGTH:
            raise ValueError(f"search_content must be at most {MAX_SEARCH_CONTENT_LENGTH} characters long.")
        return value

    @field_validator("search_filename")
    @classmethod
    def validate_search_filename(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if value == "":
            raise ValueError("search_filename must not be empty.")
        if len(value) > MAX_SEARCH_FILENAME_LENGTH:
            raise ValueError(f"search_filename must be at most {MAX_SEARCH_FILENAME_LENGTH} characters long.")
        return value

    @field_validator("search_dir")
    @classmethod
    def validate_search_dir(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if len(value) > MAX_SEARCH_DIR_LENGTH:
            raise ValueError(f"search_dir must be at most {MAX_SEARCH_DIR_LENGTH} characters long.")
        return value


class SearchResponse(BaseModel):
    files: list[str] = Field(
        ...,
        examples=[["/data/files/file1.txt", "/data/songs/song.mp3"]],
    )


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": exc.errors()})


def resolve_search_directory(search_dir: Optional[str]) -> Path:
    if search_dir is None or search_dir == "":
        candidate = DATA_DIR
    else:
        normalized = search_dir.lstrip("/")
        candidate = (DATA_DIR / normalized).resolve()

    try:
        candidate.relative_to(DATA_DIR)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="search_dir must resolve inside /data.") from exc

    if not candidate.exists() or not candidate.is_dir():
        raise HTTPException(status_code=400, detail="search_dir does not exist or is not a directory.")

    return candidate


def check_search_limits(
    start_time: float,
    files_scanned: int,
    matches_found: int,
    total_bytes_read: int,
) -> None:
    if time.monotonic() - start_time > MAX_SEARCH_DURATION_SECONDS:
        raise HTTPException(status_code=400, detail="Search exceeded allowed processing time.")
    if files_scanned > MAX_FILES_SCANNED:
        raise HTTPException(status_code=400, detail="Search exceeded maximum number of files to scan.")
    if matches_found > MAX_MATCHES:
        raise HTTPException(status_code=400, detail="Search exceeded maximum number of matches.")
    if total_bytes_read > MAX_TOTAL_BYTES_READ:
        raise HTTPException(status_code=400, detail="Search exceeded maximum content read limit.")


def content_matches(path: Path, search_content: str, total_bytes_read: int) -> tuple[bool, int]:
    try:
        stat_result = path.stat()
        if stat_result.st_size > MAX_FILE_SIZE_BYTES:
            return False, total_bytes_read

        with path.open("r", encoding="utf-8", errors="ignore") as file_obj:
            for line in file_obj:
                total_bytes_read += len(line.encode("utf-8", errors="ignore"))
                if total_bytes_read > MAX_TOTAL_BYTES_READ:
                    return False, total_bytes_read
                if search_content in line:
                    return True, total_bytes_read
    except OSError:
        return False, total_bytes_read

    return False, total_bytes_read


def file_matches(
    path: Path,
    search_content: Optional[str],
    search_filename: Optional[str],
    total_bytes_read: int,
) -> tuple[bool, int]:
    filename_match = False
    content_match = False

    if search_filename is not None:
        filename_match = path.name.startswith(search_filename)

    if search_content is not None:
        content_match, total_bytes_read = content_matches(path, search_content, total_bytes_read)

    return filename_match or content_match, total_bytes_read


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
    start_time = time.monotonic()
    files_scanned = 0
    total_bytes_read = 0

    for root, dirs, files in os.walk(search_root, topdown=True, followlinks=False):
        dirs.sort()
        files.sort()

        root_path = Path(root)
        for file_name in files:
            files_scanned += 1
            check_search_limits(start_time, files_scanned, len(matched_files), total_bytes_read)

            file_path = root_path / file_name
            if not file_path.is_file():
                continue

            matched, total_bytes_read = file_matches(
                file_path,
                request.search_content,
                request.search_filename,
                total_bytes_read,
            )

            if matched:
                matched_files.append(str(file_path.resolve()))
                check_search_limits(start_time, files_scanned, len(matched_files), total_bytes_read)

    return SearchResponse(files=matched_files)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)