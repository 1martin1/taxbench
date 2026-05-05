import os
import re
import signal
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List

import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel


BASE_DIR = Path(__file__).resolve().parent
FILES_ROOT = (BASE_DIR / "files").resolve()

MAX_REGEX_LENGTH = 256
MAX_DIRECTORY_ENTRIES = 10000
MAX_FILE_SIZE_BYTES = 1024 * 1024  # 1 MiB
READ_CHUNK_SIZE = 8192
REGEX_MATCH_TIMEOUT_SECONDS = 1


class SearchResponse(BaseModel):
    files: List[str]


class ErrorResponse(BaseModel):
    error: str


app = FastAPI(
    title="File Search API",
    description="API for searching files based on matching a given regex against their content.",
    version="1.0.0",
)


class RegexTimeoutError(Exception):
    pass


@contextmanager
def regex_timeout(seconds: int) -> Iterator[None]:
    if seconds <= 0 or os.name == "nt" or not hasattr(signal, "SIGALRM"):
        yield
        return

    def _handle_timeout(signum, frame):
        raise RegexTimeoutError()

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    signal.signal(signal.SIGALRM, _handle_timeout)
    signal.setitimer(signal.ITIMER_REAL, float(seconds))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer != (0.0, 0.0):
            signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])


def validate_regex_input(regex: str) -> None:
    if len(regex) > MAX_REGEX_LENGTH:
        raise ValueError("Invalid regex.")

    # Reject common nested-quantifier constructs that are strongly associated with ReDoS.
    # This is intentionally conservative to reduce risk while preserving API behavior.
    dangerous_patterns = (
        r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)[+*{]",
        r"\((?:[^()\\]|\\.)*[+*{](?:[^()\\]|\\.)*\)\?",
    )
    for candidate in dangerous_patterns:
        if re.search(candidate, regex):
            raise ValueError("Invalid regex.")


def resolve_search_directory(directory: str | None) -> Path:
    if directory is None or directory == "":
        target = FILES_ROOT
    else:
        directory_path = Path(directory)

        if directory_path.is_absolute():
            raise ValueError("Invalid directory.")
        if any(part == ".." for part in directory_path.parts):
            raise ValueError("Invalid directory.")

        target = (FILES_ROOT / directory_path).resolve()

    try:
        target.relative_to(FILES_ROOT)
    except ValueError:
        raise ValueError("Invalid directory.")

    if not target.exists() or not target.is_dir() or not os.access(target, os.R_OK):
        raise ValueError("Invalid directory.")

    return target


def iter_files(directory: Path, recursive: bool) -> Iterator[Path]:
    count = 0
    iterator = directory.rglob("*") if recursive else directory.iterdir()
    for path in iterator:
        count += 1
        if count > MAX_DIRECTORY_ENTRIES:
            raise ValueError("Search scope too large.")
        yield path


def file_matches_regex(file_path: Path, pattern: re.Pattern[str]) -> bool:
    if not file_path.is_file():
        return False

    try:
        stat_result = file_path.stat()
        if stat_result.st_size > MAX_FILE_SIZE_BYTES:
            return False
    except OSError:
        return False

    overlap = 1024
    previous_tail = ""

    try:
        with file_path.open("r", encoding="utf-8", errors="ignore") as file_obj:
            while True:
                chunk = file_obj.read(READ_CHUNK_SIZE)
                if chunk == "":
                    break

                content = previous_tail + chunk
                with regex_timeout(REGEX_MATCH_TIMEOUT_SECONDS):
                    if pattern.search(content) is not None:
                        return True

                if len(content) > overlap:
                    previous_tail = content[-overlap:]
                else:
                    previous_tail = content
    except (OSError, UnicodeError, RegexTimeoutError):
        return False

    return False


@app.get(
    "/search",
    response_model=SearchResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid input, such as malformed regex or inaccessible directory."},
        500: {"model": ErrorResponse, "description": "Internal server error."},
    },
    summary="Search files by regex",
    description="Searches files in a specified directory and returns files whose content matches the given regex.",
)
def search_files(
    regex: str = Query(
        ...,
        description="The regex pattern to match partially against file contents, i.e. some part of the file content must match the regex.",
        examples=[r"hello\s(you|world)"],
    ),
    directory: str | None = Query(
        None,
        description="The directory path to search files in, relative to the files root directory.",
    ),
    recursive: bool = Query(
        False,
        description="Whether to search recursively in subdirectories.",
    ),
):
    try:
        validate_regex_input(regex)
        pattern = re.compile(regex)
    except (ValueError, re.error):
        return JSONResponse(status_code=400, content={"error": "Invalid regex."})

    try:
        search_dir = resolve_search_directory(directory)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "Invalid directory."})
    except OSError:
        return JSONResponse(status_code=400, content={"error": "Invalid directory."})

    try:
        matched_files: List[str] = []
        for path in iter_files(search_dir, recursive):
            if file_matches_regex(path, pattern):
                matched_files.append(path.relative_to(search_dir).as_posix())
        return {"files": matched_files}
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "Invalid input."})
    except Exception:
        return JSONResponse(status_code=500, content={"error": "Internal server error."})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)