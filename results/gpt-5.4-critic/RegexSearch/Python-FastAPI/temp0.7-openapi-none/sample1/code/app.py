import os
import re
import threading
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

SERVER_DIR = Path(__file__).resolve().parent
FILES_ROOT = (SERVER_DIR / "files").resolve()

# Resource limits to reduce denial-of-service risk while preserving API behavior.
MAX_DIRECTORY_LENGTH = 512
MAX_PATH_PARTS = 64
MAX_FILES_SCANNED = 1000
MAX_RECURSION_DEPTH = 16
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES_SCANNED = 20 * 1024 * 1024
REGEX_MATCH_TIMEOUT_SECONDS = 0.1
MAX_CONCURRENT_SEARCHES = 4

_search_semaphore = threading.BoundedSemaphore(MAX_CONCURRENT_SEARCHES)


def error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def validate_directory_input(directory: str) -> None:
    if len(directory) > MAX_DIRECTORY_LENGTH:
        raise HTTPException(status_code=400, detail="Directory path is too long.")

    normalized = directory.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]

    if len(parts) > MAX_PATH_PARTS:
        raise HTTPException(status_code=400, detail="Directory path is too complex.")

    for part in parts:
        if part == "..":
            raise HTTPException(status_code=400, detail="Directory must be within the files root.")
        if len(part) > 255:
            raise HTTPException(status_code=400, detail="Directory path component is too long.")


def safe_search_directory(directory: str | None) -> Path:
    if directory is None or directory == "":
        target = FILES_ROOT
    else:
        validate_directory_input(directory)
        target = (FILES_ROOT / directory).resolve()

    try:
        target.relative_to(FILES_ROOT)
    except ValueError:
        raise HTTPException(status_code=400, detail="Directory must be within the files root.")

    if not target.exists():
        raise HTTPException(status_code=400, detail="Directory does not exist.")

    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Specified path is not a directory.")

    return target


def iter_files(search_dir: Path, recursive: bool) -> Iterator[Path]:
    if not recursive:
        for path in search_dir.iterdir():
            if path.is_file():
                yield path
        return

    base_depth = len(search_dir.parts)
    stack = [search_dir]

    while stack:
        current = stack.pop()
        current_depth = len(current.parts) - base_depth
        if current_depth > MAX_RECURSION_DEPTH:
            continue

        for entry in current.iterdir():
            if entry.is_file():
                yield entry
            elif entry.is_dir() and current_depth < MAX_RECURSION_DEPTH:
                stack.append(entry)


def match_with_timeout(pattern: re.Pattern[str], content: str) -> bool:
    result: dict[str, bool] = {"matched": False}
    error: dict[str, BaseException | None] = {"exception": None}

    def worker() -> None:
        try:
            result["matched"] = pattern.search(content) is not None
        except BaseException as exc:  # pragma: no cover
            error["exception"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(REGEX_MATCH_TIMEOUT_SECONDS)

    if thread.is_alive():
        raise HTTPException(status_code=400, detail="Regex evaluation exceeded allowed processing time.")

    if error["exception"] is not None:
        exc = error["exception"]
        if isinstance(exc, re.error):
            raise HTTPException(status_code=400, detail=f"Malformed regex: {exc}")
        raise exc

    return result["matched"]


def file_matches_regex(file_path: Path, pattern: re.Pattern[str]) -> bool:
    file_size = file_path.stat().st_size
    if file_size > MAX_FILE_SIZE_BYTES:
        return False

    with file_path.open("r", encoding="utf-8", errors="ignore") as f:
        content = f.read(MAX_FILE_SIZE_BYTES + 1)

    if len(content.encode("utf-8", errors="ignore")) > MAX_FILE_SIZE_BYTES:
        return False

    return match_with_timeout(pattern, content)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "Request error."
    return error_response(exc.status_code, detail)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    first_error = exc.errors()[0] if exc.errors() else None
    if first_error and "msg" in first_error:
        return error_response(400, str(first_error["msg"]))
    return error_response(400, "Invalid request parameters.")


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
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
    acquired = _search_semaphore.acquire(blocking=False)
    if not acquired:
        raise HTTPException(status_code=500, detail="Server is busy processing requests.")

    try:
        try:
            pattern = re.compile(regex)
        except re.error as exc:
            raise HTTPException(status_code=400, detail=f"Malformed regex: {exc}")

        search_dir = safe_search_directory(directory)
        matching_files: list[str] = []
        files_scanned = 0
        total_bytes_scanned = 0

        try:
            for file_path in iter_files(search_dir, recursive):
                files_scanned += 1
                if files_scanned > MAX_FILES_SCANNED:
                    raise HTTPException(status_code=400, detail="Search exceeds allowed file scan limit.")

                try:
                    file_size = file_path.stat().st_size
                except OSError as exc:
                    raise HTTPException(status_code=400, detail=f"Inaccessible directory or file: {exc}")

                if file_size <= MAX_FILE_SIZE_BYTES:
                    total_bytes_scanned += file_size
                    if total_bytes_scanned > MAX_TOTAL_BYTES_SCANNED:
                        raise HTTPException(status_code=400, detail="Search exceeds allowed data scan limit.")

                if file_matches_regex(file_path, pattern):
                    matching_files.append(file_path.relative_to(search_dir).as_posix())
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"Inaccessible directory or file: {exc}")

        return {"files": matching_files}
    finally:
        _search_semaphore.release()


if __name__ == "__main__":
    os.makedirs(FILES_ROOT, exist_ok=True)
    uvicorn.run(app, host="0.0.0.0", port=5000)