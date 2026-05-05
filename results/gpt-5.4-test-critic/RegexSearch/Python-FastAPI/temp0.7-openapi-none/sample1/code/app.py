import logging
import os
import re
from pathlib import Path
from typing import Iterator, List

import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

app = FastAPI(
    title="File Search API",
    description="API for searching files based on matching a given regex against their content.",
    version="1.0.0",
)

logger = logging.getLogger("file_search_api")
logging.basicConfig(level=logging.INFO)

SERVER_DIR = Path(__file__).resolve().parent
FILES_ROOT = (SERVER_DIR / "files").resolve()

MAX_REGEX_LENGTH = 256
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024
READ_CHUNK_SIZE = 64 * 1024


def error_response(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def resolve_search_directory(directory: str | None) -> Path:
    if directory is None or directory == "":
        target = FILES_ROOT
    else:
        target = (FILES_ROOT / directory).resolve()

    try:
        target.relative_to(FILES_ROOT)
    except ValueError as exc:
        raise ValueError("Directory must be within the files root.") from exc

    if not target.exists():
        raise ValueError("Directory does not exist.")
    if not target.is_dir():
        raise ValueError("Specified path is not a directory.")

    return target


def iter_files(directory: Path, recursive: bool) -> Iterator[Path]:
    if recursive:
        yield from (path for path in directory.rglob("*") if path.is_file())
    else:
        yield from (path for path in directory.iterdir() if path.is_file())


def validate_regex_pattern(regex: str) -> re.Pattern[str]:
    if len(regex) > MAX_REGEX_LENGTH:
        raise ValueError("Regex is too long.")

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

    nested_quantifier_patterns = (
        r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)[+*{]",
        r"\((?:[^()\\]|\\.)*\{[^}]+\}(?:[^()\\]|\\.)*\)[+*{]",
    )
    for nested_pattern in nested_quantifier_patterns:
        if re.search(nested_pattern, regex):
            raise ValueError("Regex contains potentially unsafe nested quantifiers.")

    try:
        return re.compile(regex)
    except re.error as exc:
        raise ValueError(f"Malformed regex: {exc}") from exc


def file_matches_regex(file_path: Path, pattern: re.Pattern[str]) -> bool:
    file_size = file_path.stat().st_size
    if file_size > MAX_FILE_SIZE_BYTES:
        return False

    overlap = 4096
    previous_tail = ""

    with file_path.open("r", encoding="utf-8", errors="ignore") as f:
        while True:
            chunk = f.read(READ_CHUNK_SIZE)
            if chunk == "":
                break

            text = previous_tail + chunk
            if pattern.search(text) is not None:
                return True

            if len(text) > overlap:
                previous_tail = text[-overlap:]
            else:
                previous_tail = text

    return False


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
def search_files(
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
        pattern = validate_regex_pattern(regex)
        search_dir = resolve_search_directory(directory)

        matched_files: List[str] = []
        for file_path in iter_files(search_dir, recursive):
            if file_matches_regex(file_path, pattern):
                matched_files.append(file_path.relative_to(search_dir).as_posix())

        return {"files": matched_files}
    except ValueError as exc:
        return error_response(str(exc), 400)
    except OSError:
        logger.exception("Filesystem error during search request")
        return error_response("Internal server error.", 500)


if __name__ == "__main__":
    os.makedirs(FILES_ROOT, exist_ok=True)
    uvicorn.run(app, host="0.0.0.0", port=5000)