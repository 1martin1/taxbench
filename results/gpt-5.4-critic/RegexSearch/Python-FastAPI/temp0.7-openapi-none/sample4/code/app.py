import asyncio
import os
import re
import time
from collections import deque
from pathlib import Path
from typing import Deque, Iterator, List

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

MAX_REGEX_LENGTH = 256
MAX_FILE_SIZE_BYTES = 1024 * 1024
MAX_FILES_SCANNED = 5000
MAX_RECURSION_DEPTH = 20
MAX_MATCHES_RETURNED = 10000
MAX_CONCURRENT_SEARCHES = 4
RATE_LIMIT_REQUESTS = 30
RATE_LIMIT_WINDOW_SECONDS = 60

SEARCH_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_SEARCHES)
_REQUEST_LOG: dict[str, Deque[float]] = {}


def error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def get_client_identifier(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def check_rate_limit(client_id: str) -> None:
    now = time.monotonic()
    timestamps = _REQUEST_LOG.setdefault(client_id, deque())

    while timestamps and now - timestamps[0] > RATE_LIMIT_WINDOW_SECONDS:
        timestamps.popleft()

    if len(timestamps) >= RATE_LIMIT_REQUESTS:
        raise HTTPException(status_code=429, detail="Too many requests.")

    timestamps.append(now)

    if not timestamps:
        _REQUEST_LOG.pop(client_id, None)


def resolve_search_directory(directory: str | None) -> Path:
    if directory is None or directory == "":
        target = FILES_ROOT
    else:
        target = (FILES_ROOT / directory).resolve()

    try:
        target.relative_to(FILES_ROOT)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid directory: path must be within the files root.",
        ) from exc

    if not target.exists():
        raise HTTPException(
            status_code=400,
            detail="Invalid directory: directory does not exist.",
        )

    if not target.is_dir():
        raise HTTPException(
            status_code=400,
            detail="Invalid directory: path is not a directory.",
        )

    if not os.access(target, os.R_OK):
        raise HTTPException(
            status_code=400,
            detail="Invalid directory: directory is not accessible.",
        )

    return target


def compile_pattern(regex: str) -> re.Pattern[str]:
    if len(regex) > MAX_REGEX_LENGTH:
        raise HTTPException(
            status_code=400,
            detail="Invalid regex: pattern is too long.",
        )

    risky_tokens = (
        "++",
        "*+",
        "?+",
        "{",
        "(?=",
        "(?!",
        "(?<=",
        "(?<!",
        "(?>",
        "(?P=",
        "\\1",
        "\\2",
        "\\3",
        "\\4",
        "\\5",
        "\\6",
        "\\7",
        "\\8",
        "\\9",
    )
    if any(token in regex for token in risky_tokens):
        raise HTTPException(
            status_code=400,
            detail="Invalid regex: pattern uses unsupported or excessively complex constructs.",
        )

    try:
        return re.compile(regex)
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {exc}") from exc


def iter_files(search_dir: Path, recursive: bool) -> Iterator[Path]:
    scanned = 0

    if not recursive:
        try:
            for entry in search_dir.iterdir():
                scanned += 1
                if scanned > MAX_FILES_SCANNED:
                    raise HTTPException(
                        status_code=400,
                        detail="Search exceeds maximum allowed number of filesystem entries.",
                    )
                yield entry
        except OSError as exc:
            raise HTTPException(
                status_code=400,
                detail="Invalid directory: directory is not accessible.",
            ) from exc
        return

    stack: List[tuple[Path, int]] = [(search_dir, 0)]
    while stack:
        current_dir, depth = stack.pop()
        if depth > MAX_RECURSION_DEPTH:
            raise HTTPException(
                status_code=400,
                detail="Search exceeds maximum allowed recursion depth.",
            )

        try:
            entries = list(current_dir.iterdir())
        except OSError as exc:
            raise HTTPException(
                status_code=400,
                detail="Invalid directory: directory is not accessible.",
            ) from exc

        for entry in entries:
            scanned += 1
            if scanned > MAX_FILES_SCANNED:
                raise HTTPException(
                    status_code=400,
                    detail="Search exceeds maximum allowed number of filesystem entries.",
                )

            yield entry

            try:
                if entry.is_dir():
                    stack.append((entry, depth + 1))
            except OSError:
                continue


def file_matches(pattern: re.Pattern[str], file_path: Path) -> bool:
    try:
        if not file_path.is_file():
            return False
    except OSError:
        return False

    if not os.access(file_path, os.R_OK):
        return False

    try:
        if file_path.stat().st_size > MAX_FILE_SIZE_BYTES:
            return False
    except OSError:
        return False

    try:
        with file_path.open("r", encoding="utf-8", errors="ignore") as f:
            for chunk in iter(lambda: f.read(8192), ""):
                if pattern.search(chunk) is not None:
                    return True
        return False
    except (OSError, UnicodeError, re.error):
        return False


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    message = str(exc.detail) if exc.detail is not None else "Request failed."
    return error_response(exc.status_code, message)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError):
    return error_response(status_code=400, message=str(exc))


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, __: Exception):
    return error_response(status_code=500, message="Internal server error.")


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
)
async def search_files(
    request: Request,
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
    check_rate_limit(get_client_identifier(request))

    async with SEARCH_SEMAPHORE:
        search_dir = resolve_search_directory(directory)
        pattern = compile_pattern(regex)

        matched_files: List[str] = []
        for path in iter_files(search_dir, recursive):
            if file_matches(pattern, path):
                matched_files.append(path.relative_to(search_dir).as_posix())
                if len(matched_files) > MAX_MATCHES_RETURNED:
                    raise HTTPException(
                        status_code=400,
                        detail="Search exceeds maximum allowed number of matching files.",
                    )

        matched_files.sort()
        return {"files": matched_files}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)