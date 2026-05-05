import os
import re
from pathlib import Path
from typing import List

import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel


BASE_DIR = Path(__file__).resolve().parent
FILES_ROOT = (BASE_DIR / "files").resolve()


class SearchResponse(BaseModel):
    files: List[str]


class ErrorResponse(BaseModel):
    error: str


app = FastAPI(
    title="File Search API",
    description="API for searching files based on matching a given regex against their content.",
    version="1.0.0",
)


def safe_resolve_search_dir(directory: str | None) -> Path:
    if directory is None or directory == "":
        target = FILES_ROOT
    else:
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


def iter_files(search_dir: Path, recursive: bool):
    if recursive:
        yield from (p for p in search_dir.rglob("*") if p.is_file())
    else:
        yield from (p for p in search_dir.iterdir() if p.is_file())


def file_matches_regex(file_path: Path, pattern: re.Pattern[str]) -> bool:
    try:
        with file_path.open("r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        return pattern.search(content) is not None
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
):
    try:
        try:
            pattern = re.compile(regex)
        except re.error as exc:
            return JSONResponse(
                status_code=400,
                content={"error": f"Malformed regex: {exc}"},
            )

        try:
            search_dir = safe_resolve_search_dir(directory)
        except ValueError as exc:
            return JSONResponse(
                status_code=400,
                content={"error": str(exc)},
            )

        matches: List[str] = []
        for file_path in iter_files(search_dir, recursive):
            if file_matches_regex(file_path, pattern):
                matches.append(file_path.relative_to(search_dir).as_posix())

        return {"files": matches}
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": f"Internal server error: {exc}"},
        )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)