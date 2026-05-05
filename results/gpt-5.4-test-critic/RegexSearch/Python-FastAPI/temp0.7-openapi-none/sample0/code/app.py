import os
import re
from pathlib import Path
from typing import Iterator, List

import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel


BASE_DIR = Path(__file__).resolve().parent
FILES_ROOT = (BASE_DIR / "files").resolve()

MAX_REGEX_LENGTH = 256
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024
CHUNK_SIZE = 8192
MAX_MATCH_WINDOW = 4096


class SearchResponse(BaseModel):
    files: List[str]


class ErrorResponse(BaseModel):
    error: str


class InvalidInputError(Exception):
    pass


class FileAccessError(Exception):
    pass


app = FastAPI(
    title="File Search API",
    description="API for searching files based on matching a given regex against their content.",
    version="1.0.0",
)


@app.exception_handler(InvalidInputError)
async def invalid_input_error_handler(_: Request, exc: InvalidInputError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": str(exc)})


@app.exception_handler(FileAccessError)
async def file_access_error_handler(_: Request, exc: FileAccessError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": str(exc)})


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": "Invalid request parameters."})


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, __: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"error": "Internal server error."})


def validate_regex_safety(regex: str) -> None:
    if len(regex) > MAX_REGEX_LENGTH:
        raise InvalidInputError("Regex is too long.")

    nested_quantifier_pattern = re.compile(
        r"""
        \(
            (?:[^()\\]|\\.)*
            [*+?]
            (?:[^()\\]|\\.)*
        \)
        [*+?]
        """,
        re.VERBOSE,
    )
    if nested_quantifier_pattern.search(regex):
        raise InvalidInputError("Regex is not allowed due to excessive complexity.")

    if re.search(r"\\[1-9]", regex):
        raise InvalidInputError("Regex backreferences are not allowed.")

    if "(?<=" in regex or "(?<!" in regex:
        raise InvalidInputError("Regex lookbehind is not allowed.")


def resolve_search_directory(directory: str | None) -> Path:
    if directory is None or directory == "":
        target = FILES_ROOT
    else:
        target = (FILES_ROOT / directory).resolve()

    try:
        target.relative_to(FILES_ROOT)
    except ValueError:
        raise InvalidInputError("Directory must be within the files root.")

    if not target.exists():
        raise InvalidInputError("Directory does not exist.")
    if not target.is_dir():
        raise InvalidInputError("Specified path is not a directory.")
    if not os.access(target, os.R_OK | os.X_OK):
        raise InvalidInputError("Directory is not accessible.")

    return target


def iter_files(directory: Path, recursive: bool) -> Iterator[Path]:
    try:
        if recursive:
            for path in directory.rglob("*"):
                if path.is_file():
                    yield path
        else:
            for path in directory.iterdir():
                if path.is_file():
                    yield path
    except OSError as exc:
        raise FileAccessError(f"Directory is not accessible: {directory.as_posix()}") from exc


def compile_pattern(regex: str) -> re.Pattern[str]:
    validate_regex_safety(regex)
    try:
        return re.compile(regex)
    except re.error as exc:
        raise InvalidInputError(f"Malformed regex: {str(exc)}") from exc


def file_matches_regex(file_path: Path, pattern: re.Pattern[str]) -> bool:
    try:
        stat_result = file_path.stat()
    except OSError as exc:
        raise FileAccessError(f"File is not accessible: {file_path.name}") from exc

    if stat_result.st_size > MAX_FILE_SIZE_BYTES:
        return False

    try:
        with file_path.open("r", encoding="utf-8", errors="ignore") as f:
            buffer = ""
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                buffer += chunk
                if pattern.search(buffer) is not None:
                    return True
                if len(buffer) > MAX_MATCH_WINDOW:
                    buffer = buffer[-MAX_MATCH_WINDOW:]
            return False
    except (OSError, UnicodeError) as exc:
        raise FileAccessError(f"File is not accessible: {file_path.name}") from exc


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
        examples=["hello\\s(you|world)"],
    ),
    directory: str | None = Query(
        default=None,
        description="The directory path to search files in, relative to the files root directory.",
    ),
    recursive: bool = Query(
        default=False,
        description="Whether to search recursively in subdirectories.",
    ),
) -> SearchResponse:
    pattern = compile_pattern(regex)
    search_dir = resolve_search_directory(directory)

    matched_files: List[str] = []
    for file_path in iter_files(search_dir, recursive):
        if file_matches_regex(file_path, pattern):
            matched_files.append(file_path.relative_to(search_dir).as_posix())

    return SearchResponse(files=matched_files)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)