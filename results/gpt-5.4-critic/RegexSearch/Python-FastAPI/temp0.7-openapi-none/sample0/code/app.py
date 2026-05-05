import os
import re
from pathlib import Path
from typing import Iterator

import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI(
    title="File Search API",
    description="API for searching files based on matching a given regex against their content.",
    version="1.0.0",
)

SERVER_DIR = Path(__file__).resolve().parent
FILES_ROOT = (SERVER_DIR / "files").resolve()

MAX_REGEX_LENGTH = 256
MAX_DIRECTORY_LENGTH = 256
MAX_FILES_SCANNED = 5000
MAX_RECURSION_DEPTH = 10
MAX_FILE_SIZE_BYTES = 1024 * 1024  # 1 MiB
CHUNK_SIZE = 8192


def error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def resolve_search_directory(directory: str | None) -> Path:
    if directory is None or directory == "":
        target = FILES_ROOT
    else:
        if len(directory) > MAX_DIRECTORY_LENGTH:
            raise ValueError("Directory path is too long.")
        if "\x00" in directory:
            raise ValueError("Directory path contains invalid characters.")

        target = (FILES_ROOT / directory).resolve()

    try:
        target.relative_to(FILES_ROOT)
    except ValueError:
        raise ValueError("Directory must be within the files root.")

    if not target.exists():
        raise ValueError("Directory does not exist.")
    if not target.is_dir():
        raise ValueError("Specified path is not a directory.")
    if not os.access(target, os.R_OK):
        raise ValueError("Directory is not accessible.")

    return target


def validate_regex(regex: str) -> re.Pattern[str]:
    if len(regex) > MAX_REGEX_LENGTH:
        raise ValueError("Regex pattern is too long.")

    disallowed_tokens = (
        "(?=",
        "(?!",
        "(?<=",
        "(?<!",
        "(?>",
        "(?#",
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
    if any(token in regex for token in disallowed_tokens):
        raise ValueError("Regex contains unsupported constructs.")

    nested_quantifier_pattern = re.compile(r"(\([^)]*[+*][^)]*\)|\[[^\]]+\])[+*{]")
    if nested_quantifier_pattern.search(regex):
        raise ValueError("Regex contains unsupported nested quantifiers.")

    try:
        return re.compile(regex)
    except re.error as exc:
        raise ValueError(f"Malformed regex: {exc}") from exc


def iter_files(search_dir: Path, recursive: bool) -> Iterator[Path]:
    scanned = 0

    if recursive:
        base_depth = len(search_dir.parts)
        stack = [search_dir]

        while stack:
            current_dir = stack.pop()

            try:
                entries = list(current_dir.iterdir())
            except OSError as exc:
                raise OSError(f"Cannot access directory '{current_dir}': {exc}") from exc

            for entry in entries:
                try:
                    if entry.is_symlink():
                        continue

                    if entry.is_dir():
                        depth = len(entry.parts) - base_depth
                        if depth <= MAX_RECURSION_DEPTH:
                            stack.append(entry)
                        continue

                    if entry.is_file():
                        scanned += 1
                        if scanned > MAX_FILES_SCANNED:
                            raise ValueError("Search exceeds allowed file scan limit.")
                        yield entry
                except OSError:
                    continue
    else:
        try:
            entries = list(search_dir.iterdir())
        except OSError as exc:
            raise OSError(f"Cannot access directory '{search_dir}': {exc}") from exc

        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_file():
                    scanned += 1
                    if scanned > MAX_FILES_SCANNED:
                        raise ValueError("Search exceeds allowed file scan limit.")
                    yield entry
            except OSError:
                continue


def file_matches_regex(file_path: Path, pattern: re.Pattern[str]) -> bool:
    try:
        stat_result = file_path.stat()
        if stat_result.st_size > MAX_FILE_SIZE_BYTES:
            return False

        with file_path.open("r", encoding="utf-8", errors="ignore") as f:
            overlap = ""
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break

                data = overlap + chunk
                if pattern.search(data) is not None:
                    return True

                overlap = data[-MAX_REGEX_LENGTH:] if len(data) > MAX_REGEX_LENGTH else data

        return False
    except (OSError, UnicodeError):
        return False


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request, exc: StarletteHTTPException):
    if exc.status_code >= 500:
        return error_response(500, "Internal server error.")
    return error_response(exc.status_code, str(exc.detail))


@app.get(
    "/search",
    responses={
        200: {
            "description": "A list of files matching the regex, specifying the path relative to the indicated directory."
        },
        400: {"description": "Invalid input, such as malformed regex or inaccessible directory."},
        500: {"description": "Internal server error."},
    },
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
    try:
        pattern = validate_regex(regex)
        search_dir = resolve_search_directory(directory)
    except ValueError as exc:
        return error_response(400, str(exc))

    matching_files: list[str] = []

    try:
        for file_path in iter_files(search_dir, recursive):
            if file_matches_regex(file_path, pattern):
                relative_path = file_path.relative_to(search_dir).as_posix()
                matching_files.append(relative_path)
    except ValueError as exc:
        return error_response(400, str(exc))
    except OSError as exc:
        return error_response(400, f"Inaccessible directory: {exc}")
    except Exception:
        return error_response(500, "Internal server error.")

    return {"files": matching_files}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)