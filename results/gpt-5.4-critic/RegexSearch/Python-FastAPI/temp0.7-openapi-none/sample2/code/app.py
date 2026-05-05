import os
import re
import time
from pathlib import Path
from typing import Iterator

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

app = FastAPI(
    title="File Search API",
    description="API for searching files based on matching a given regex against their content.",
    version="1.0.0",
)

FILES_ROOT = (Path(__file__).resolve().parent / "files").resolve()

MAX_DIRECTORY_LENGTH = 1024
MAX_REGEX_LENGTH = 512
MAX_FILES_SCANNED = 10000
MAX_MATCHED_FILES = 1000
MAX_RECURSION_DEPTH = 25
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024
MAX_FILE_CHARS_TO_SCAN = 5 * 1024 * 1024
SEARCH_TIME_LIMIT_SECONDS = 5.0
REGEX_COMPLEXITY_TOKENS = ("(?=", "(?!", "(?<=", "(?<!", "\\1", "\\2", "\\3", "\\4", "\\5", "\\6", "\\7", "\\8", "\\9")


def error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def validate_regex_input(regex: str) -> None:
    if len(regex) > MAX_REGEX_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid regex pattern.")
    if any(token in regex for token in REGEX_COMPLEXITY_TOKENS):
        raise HTTPException(status_code=400, detail="Invalid regex pattern.")


def resolve_search_directory(directory: str | None) -> Path:
    if directory is None or directory == "":
        target = FILES_ROOT
    else:
        if len(directory) > MAX_DIRECTORY_LENGTH:
            raise HTTPException(status_code=400, detail="Invalid directory.")
        if "\x00" in directory:
            raise HTTPException(status_code=400, detail="Invalid directory.")
        target = (FILES_ROOT / directory).resolve()

    try:
        target.relative_to(FILES_ROOT)
    except ValueError:
        raise HTTPException(status_code=400, detail="Directory must be within the files root.")

    if not target.exists():
        raise HTTPException(status_code=400, detail="Directory does not exist.")

    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Specified path is not a directory.")

    if not os.access(target, os.R_OK):
        raise HTTPException(status_code=400, detail="Directory is not accessible.")

    return target


def iter_files_non_recursive(search_dir: Path) -> Iterator[Path]:
    yield from search_dir.iterdir()


def iter_files_recursive(search_dir: Path) -> Iterator[Path]:
    stack: list[tuple[Path, int]] = [(search_dir, 0)]
    while stack:
        current_dir, depth = stack.pop()
        try:
            entries = list(current_dir.iterdir())
        except OSError:
            continue

        for entry in entries:
            yield entry
            if entry.is_dir() and depth < MAX_RECURSION_DEPTH:
                stack.append((entry, depth + 1))


def iter_files(search_dir: Path, recursive: bool) -> Iterator[Path]:
    if recursive:
        yield from iter_files_recursive(search_dir)
    else:
        yield from iter_files_non_recursive(search_dir)


def file_matches_regex(file_path: Path, pattern: re.Pattern[str], deadline: float) -> bool:
    if time.monotonic() > deadline:
        raise HTTPException(status_code=400, detail="Search limits exceeded.")

    if not file_path.is_file():
        return False

    if not os.access(file_path, os.R_OK):
        return False

    try:
        stat_result = file_path.stat()
    except OSError:
        return False

    if stat_result.st_size > MAX_FILE_SIZE_BYTES:
        return False

    total_chars = 0
    try:
        with file_path.open("r", encoding="utf-8", errors="ignore") as f:
            while True:
                if time.monotonic() > deadline:
                    raise HTTPException(status_code=400, detail="Search limits exceeded.")
                chunk = f.read(8192)
                if not chunk:
                    break
                total_chars += len(chunk)
                if total_chars > MAX_FILE_CHARS_TO_SCAN:
                    return False
                if pattern.search(chunk) is not None:
                    return True
    except HTTPException:
        raise
    except OSError:
        return False

    return False


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(_: Request, __: RequestValidationError):
    return error_response(400, "Invalid input.")


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    if exc.status_code not in (400, 500):
        return error_response(500, "Internal server error.")
    return error_response(exc.status_code, str(exc.detail))


@app.exception_handler(Exception)
async def general_exception_handler(_: Request, __: Exception):
    return error_response(500, "Internal server error.")


@app.get(
    "/search",
    responses={
        200: {
            "description": "A list of files matching the regex, specifying the path relative to the indicated directory.",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "files": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of file paths relative to search directory that match the regex.",
                                "example": ["folder1/file1.txt", "file2.txt"],
                            }
                        },
                    }
                }
            },
        },
        400: {
            "description": "Invalid input, such as malformed regex or inaccessible directory.",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "error": {
                                "type": "string",
                                "description": "Error message detailing the issue.",
                            }
                        },
                    }
                }
            },
        },
        500: {
            "description": "Internal server error.",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "error": {
                                "type": "string",
                                "description": "Error message detailing the server issue.",
                            }
                        },
                    }
                }
            },
        },
    },
    summary="Search files by regex",
    description="Searches files in a specified directory and returns files whose content matches the given regex.",
)
async def search_files(
    regex: str = Query(
        ...,
        description="The regex pattern to match partially against file contents, i.e. some part of the file content must match the regex.",
        example="hello\\s(you|world)",
    ),
    directory: str | None = Query(
        default=None,
        description="The directory path to search files in, relative to the files root directory.",
    ),
    recursive: bool = Query(
        default=False,
        description="Whether to search recursively in subdirectories.",
    ),
):
    validate_regex_input(regex)

    try:
        pattern = re.compile(regex)
    except re.error:
        raise HTTPException(status_code=400, detail="Malformed regex.")

    search_dir = resolve_search_directory(directory)
    deadline = time.monotonic() + SEARCH_TIME_LIMIT_SECONDS

    matched_files: list[str] = []
    scanned_files = 0

    for path in iter_files(search_dir, recursive):
        if time.monotonic() > deadline:
            raise HTTPException(status_code=400, detail="Search limits exceeded.")

        if not path.is_file():
            continue

        scanned_files += 1
        if scanned_files > MAX_FILES_SCANNED:
            raise HTTPException(status_code=400, detail="Search limits exceeded.")

        if file_matches_regex(path, pattern, deadline):
            matched_files.append(path.relative_to(search_dir).as_posix())
            if len(matched_files) > MAX_MATCHED_FILES:
                raise HTTPException(status_code=400, detail="Search limits exceeded.")

    matched_files.sort()
    return {"files": matched_files}


if __name__ == "__main__":
    FILES_ROOT.mkdir(parents=True, exist_ok=True)
    uvicorn.run(app, host="0.0.0.0", port=5000)