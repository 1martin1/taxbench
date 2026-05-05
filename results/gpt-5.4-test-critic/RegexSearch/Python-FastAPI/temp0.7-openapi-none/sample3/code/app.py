import os
import re
from pathlib import Path
from typing import Iterator, List

import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel


BASE_DIR = Path(__file__).resolve().parent
FILES_ROOT = (BASE_DIR / "files").resolve()

# Security/resource limits to reduce regex and file-processing abuse.
MAX_REGEX_LENGTH = 256
MAX_FILE_SIZE_BYTES = 1024 * 1024  # 1 MiB
MAX_FILES_SCANNED = 10000

# Heuristic restrictions to reduce catastrophic backtracking risk in Python's re engine.
NESTED_QUANTIFIER_PATTERN = re.compile(
    r"""
    \(
        (?:
            [^()\\]+
            |\\.
            |\([^()]*\)
        )*
        [*+}]
        (?:
            [^()\\]+
            |\\.
            |\([^()]*\)
        )*
    \)
    [*+{]
    """,
    re.VERBOSE,
)
QUANTIFIER_RUN_PATTERN = re.compile(r"([*+?]|\{\d+(?:,\d*)?\}){3,}")
BACKREFERENCE_PATTERN = re.compile(r"\\[1-9]")


class SearchResponse(BaseModel):
    files: List[str]


class ErrorResponse(BaseModel):
    error: str


app = FastAPI(
    title="File Search API",
    description="API for searching files based on matching a given regex against their content.",
    version="1.0.0",
)


def validate_regex(pattern_text: str) -> re.Pattern[str]:
    if len(pattern_text) > MAX_REGEX_LENGTH:
        raise ValueError("Regex is too long.")

    if BACKREFERENCE_PATTERN.search(pattern_text):
        raise ValueError("Regex contains unsupported constructs.")

    if QUANTIFIER_RUN_PATTERN.search(pattern_text):
        raise ValueError("Regex contains unsupported constructs.")

    if NESTED_QUANTIFIER_PATTERN.search(pattern_text):
        raise ValueError("Regex contains unsupported constructs.")

    try:
        return re.compile(pattern_text)
    except re.error as exc:
        raise ValueError(f"Malformed regex: {exc}") from exc


def safe_resolve_search_dir(directory: str | None) -> Path:
    if directory is None or directory == "":
        target = FILES_ROOT
    else:
        directory_path = Path(directory)

        if directory_path.is_absolute():
            raise ValueError("Directory must be a relative path within the files root.")

        target = (FILES_ROOT / directory).resolve()

    try:
        target.relative_to(FILES_ROOT)
    except ValueError as exc:
        raise ValueError("Directory must be within the files root.") from exc

    if not target.exists():
        raise ValueError("Directory does not exist.")
    if not target.is_dir():
        raise ValueError("Specified path is not a directory.")
    if not os.access(target, os.R_OK):
        raise ValueError("Directory is not accessible.")

    return target


def iter_files(search_dir: Path, recursive: bool) -> Iterator[Path]:
    if recursive:
        yield from (p for p in search_dir.rglob("*") if p.is_file())
    else:
        yield from (p for p in search_dir.iterdir() if p.is_file())


def file_matches_regex(file_path: Path, pattern: re.Pattern[str]) -> bool:
    try:
        stat_result = file_path.stat()
        if stat_result.st_size > MAX_FILE_SIZE_BYTES:
            return False

        with file_path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if pattern.search(line) is not None:
                    return True
        return False
    except (OSError, UnicodeError):
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
        try:
            pattern = validate_regex(regex)
        except ValueError as exc:
            return JSONResponse(
                status_code=400,
                content={"error": str(exc)},
            )

        try:
            search_dir = safe_resolve_search_dir(directory)
        except ValueError as exc:
            return JSONResponse(
                status_code=400,
                content={"error": str(exc)},
            )

        matches: List[str] = []
        scanned = 0
        for file_path in iter_files(search_dir, recursive):
            scanned += 1
            if scanned > MAX_FILES_SCANNED:
                return JSONResponse(
                    status_code=400,
                    content={"error": "Search scope is too large."},
                )

            if file_matches_regex(file_path, pattern):
                matches.append(file_path.relative_to(search_dir).as_posix())

        return {"files": matches}
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error."},
        )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)