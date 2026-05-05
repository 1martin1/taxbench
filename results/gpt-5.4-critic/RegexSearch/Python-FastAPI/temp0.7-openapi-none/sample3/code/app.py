import os
import re
import signal
from pathlib import Path
from typing import Iterator

import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.responses import Response
from starlette.status import HTTP_400_BAD_REQUEST, HTTP_500_INTERNAL_SERVER_ERROR

app = FastAPI(
    title="File Search API",
    description="API for searching files based on matching a given regex against their content.",
    version="1.0.0",
)

FILES_ROOT = (Path(__file__).resolve().parent / "files").resolve()

MAX_REGEX_LENGTH = 512
MAX_DIRECTORY_LENGTH = 1024
MAX_FILES_SCANNED = 5000
MAX_RESULTS = 1000
MAX_FILE_SIZE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES_SCANNED = 20 * 1024 * 1024
REGEX_TIMEOUT_SECONDS = 1


class SearchInputError(ValueError):
    pass


class RegexTimeoutError(Exception):
    pass


def error_response(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def resolve_search_directory(directory: str | None) -> Path:
    base = FILES_ROOT

    if directory is not None:
        if len(directory) > MAX_DIRECTORY_LENGTH:
            raise SearchInputError("Directory path is too long.")
        if "\x00" in directory:
            raise SearchInputError("Directory path is invalid.")

    if directory is None or directory == "":
        target = base
    else:
        target = (base / directory).resolve()

    try:
        target.relative_to(base)
    except ValueError as exc:
        raise SearchInputError("Directory must be within the files root.") from exc

    if not target.exists():
        raise SearchInputError("Directory does not exist.")
    if not target.is_dir():
        raise SearchInputError("Specified path is not a directory.")
    if not os.access(target, os.R_OK | os.X_OK):
        raise SearchInputError("Directory is not accessible.")

    return target


def iter_files(directory: Path, recursive: bool) -> Iterator[Path]:
    iterator = directory.rglob("*") if recursive else directory.glob("*")
    for path in iterator:
        yield path


def validate_regex(regex: str) -> re.Pattern[str]:
    if len(regex) > MAX_REGEX_LENGTH:
        raise SearchInputError("Regex is too long.")
    try:
        return re.compile(regex)
    except re.error as exc:
        raise SearchInputError(f"Invalid regex: {str(exc)}") from exc


def _timeout_handler(signum, frame):
    raise RegexTimeoutError()


def regex_search_with_timeout(pattern: re.Pattern[str], content: str) -> bool:
    if hasattr(signal, "SIGALRM"):
        previous_handler = signal.getsignal(signal.SIGALRM)
        try:
            signal.signal(signal.SIGALRM, _timeout_handler)
            signal.setitimer(signal.ITIMER_REAL, REGEX_TIMEOUT_SECONDS)
            return pattern.search(content) is not None
        except RegexTimeoutError as exc:
            raise SearchInputError("Regex evaluation timed out.") from exc
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
    return pattern.search(content) is not None


def file_matches_regex(file_path: Path, pattern: re.Pattern[str]) -> bool:
    if not file_path.is_file():
        return False

    try:
        if not os.access(file_path, os.R_OK):
            raise SearchInputError(f"Inaccessible file encountered: {file_path.name}")

        stat_result = file_path.stat()
        if stat_result.st_size > MAX_FILE_SIZE_BYTES:
            return False

        with file_path.open("r", encoding="utf-8", errors="ignore") as f:
            content = f.read(MAX_FILE_SIZE_BYTES + 1)
    except SearchInputError:
        raise
    except OSError as exc:
        raise SearchInputError(f"Inaccessible file encountered: {file_path.name}") from exc

    if len(content.encode("utf-8", errors="ignore")) > MAX_FILE_SIZE_BYTES:
        return False

    return regex_search_with_timeout(pattern, content)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> Response:
    if isinstance(exc, SearchInputError):
        return error_response(str(exc), HTTP_400_BAD_REQUEST)
    return error_response("Internal server error.", HTTP_500_INTERNAL_SERVER_ERROR)


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
        examples={
            "default": {
                "summary": "Example regex",
                "value": r"hello\s(you|world)",
            }
        },
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
    try:
        pattern = validate_regex(regex)
        search_dir = resolve_search_directory(directory)

        matched_files: list[str] = []
        files_scanned = 0
        total_bytes_scanned = 0

        for path in iter_files(search_dir, recursive):
            if not path.is_file():
                continue

            files_scanned += 1
            if files_scanned > MAX_FILES_SCANNED:
                raise SearchInputError("Search exceeds allowed file scan limit.")

            try:
                stat_result = path.stat()
            except OSError as exc:
                raise SearchInputError(f"Inaccessible file encountered: {path.name}") from exc

            if not os.access(path, os.R_OK):
                raise SearchInputError(f"Inaccessible file encountered: {path.name}")

            if stat_result.st_size <= MAX_FILE_SIZE_BYTES:
                total_bytes_scanned += stat_result.st_size
                if total_bytes_scanned > MAX_TOTAL_BYTES_SCANNED:
                    raise SearchInputError("Search exceeds allowed total scan size.")

            if file_matches_regex(path, pattern):
                matched_files.append(path.relative_to(search_dir).as_posix())
                if len(matched_files) >= MAX_RESULTS:
                    break

        matched_files.sort()
        return {"files": matched_files}
    except SearchInputError as exc:
        return error_response(str(exc), HTTP_400_BAD_REQUEST)
    except Exception:
        return error_response("Internal server error.", HTTP_500_INTERNAL_SERVER_ERROR)


if __name__ == "__main__":
    FILES_ROOT.mkdir(parents=True, exist_ok=True)
    uvicorn.run(app, host="0.0.0.0", port=5000)