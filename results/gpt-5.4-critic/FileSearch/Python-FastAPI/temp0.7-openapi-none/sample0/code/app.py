import os
from pathlib import Path
from typing import List, Optional

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

DATA_ROOT = Path("/data").resolve()

# Resource limits to reduce uncontrolled resource consumption while preserving API behavior.
MAX_INPUT_LENGTH = 4096
MAX_FILES_SCANNED = 10000
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES_READ = 20 * 1024 * 1024
MAX_RESULTS = 1000
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

    search_content: Optional[str] = Field(default=None, max_length=MAX_INPUT_LENGTH, example="Hello World")
    search_filename: Optional[str] = Field(default=None, max_length=MAX_INPUT_LENGTH, example="file.txt")
    search_dir: Optional[str] = Field(default=None, max_length=MAX_INPUT_LENGTH, example="/songs")


class SearchResponse(BaseModel):
    files: List[str] = Field(
        ...,
        example=["/data/files/file1.txt", "/data/songs/song.mp3"],
    )


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": "Invalid input"})


def validate_payload(payload: SearchRequest) -> None:
    if not payload.search_content and not payload.search_filename:
        raise HTTPException(status_code=400, detail="Invalid input")


def resolve_search_directory(search_dir: Optional[str]) -> Path:
    if search_dir is None or search_dir == "":
        candidate = DATA_ROOT
    else:
        normalized = search_dir.lstrip("/\\")
        candidate = (DATA_ROOT / normalized).resolve()

    try:
        candidate.relative_to(DATA_ROOT)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid input")

    if not candidate.exists() or not candidate.is_dir():
        raise HTTPException(status_code=400, detail="Invalid input")

    return candidate


def safe_result_path(file_path: Path) -> Optional[str]:
    try:
        resolved_file = file_path.resolve()
        resolved_file.relative_to(DATA_ROOT)
        return str(resolved_file)
    except (OSError, ValueError):
        return None


def file_contains_text(file_path: Path, needle: str, remaining_budget: int) -> tuple[bool, int]:
    if remaining_budget <= 0:
        return False, 0

    try:
        stat_result = file_path.stat()
        if not file_path.is_file():
            return False, 0
        if stat_result.st_size > MAX_FILE_SIZE_BYTES:
            return False, 0
    except OSError:
        return False, 0

    bytes_read = 0
    overlap = max(len(needle) - 1, 0)
    previous_tail = ""

    try:
        with file_path.open("r", encoding="utf-8", errors="ignore") as file:
            while bytes_read < remaining_budget:
                to_read = min(READ_CHUNK_SIZE, remaining_budget - bytes_read)
                if to_read <= 0:
                    break

                chunk = file.read(to_read)
                if chunk == "":
                    break

                bytes_read += len(chunk)
                haystack = previous_tail + chunk
                if needle in haystack:
                    return True, bytes_read

                if overlap > 0:
                    previous_tail = haystack[-overlap:]
                else:
                    previous_tail = ""
    except (OSError, UnicodeError):
        return False, bytes_read

    return False, bytes_read


@app.post("/search", response_model=SearchResponse, responses={400: {"description": "Invalid input"}})
def search_files(payload: SearchRequest = Body(...)) -> SearchResponse:
    validate_payload(payload)
    search_root = resolve_search_directory(payload.search_dir)

    matched_files: List[str] = []
    files_scanned = 0
    total_bytes_read = 0

    for root, dirs, files in os.walk(search_root, topdown=True, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        root_path = Path(root)

        try:
            root_path.resolve().relative_to(DATA_ROOT)
        except (OSError, ValueError):
            continue

        for filename in sorted(files):
            if files_scanned >= MAX_FILES_SCANNED or len(matched_files) >= MAX_RESULTS:
                return SearchResponse(files=matched_files)

            file_path = root_path / filename
            files_scanned += 1
            matched = False

            if payload.search_filename is not None and filename.startswith(payload.search_filename):
                matched = True

            if not matched and payload.search_content is not None and total_bytes_read < MAX_TOTAL_BYTES_READ:
                contains, consumed = file_contains_text(
                    file_path=file_path,
                    needle=payload.search_content,
                    remaining_budget=MAX_TOTAL_BYTES_READ - total_bytes_read,
                )
                total_bytes_read += consumed
                if contains:
                    matched = True

            if matched:
                result_path = safe_result_path(file_path)
                if result_path is not None:
                    matched_files.append(result_path)
                    if len(matched_files) >= MAX_RESULTS:
                        return SearchResponse(files=matched_files)

    return SearchResponse(files=matched_files)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)