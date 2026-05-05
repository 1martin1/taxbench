import os
import re
from pathlib import Path
from typing import List

import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

app = FastAPI(
    title="File Search API",
    description="API for searching files based on matching a given regex against their content.",
    version="1.0.0",
)

SERVER_DIR = Path(__file__).resolve().parent
FILES_ROOT = (SERVER_DIR / "files").resolve()


def error_response(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def resolve_search_directory(directory: str | None) -> Path:
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


def iter_files(directory: Path, recursive: bool):
    if recursive:
        yield from directory.rglob("*")
    else:
        yield from directory.iterdir()


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
        pattern = re.compile(regex)
    except re.error as exc:
        return error_response(f"Malformed regex: {exc}", 400)

    try:
        search_dir = resolve_search_directory(directory)
    except ValueError as exc:
        return error_response(str(exc), 400)
    except Exception as exc:
        return error_response(f"Internal server error: {exc}", 500)

    matches: List[str] = []

    try:
        for path in iter_files(search_dir, recursive):
            if not path.is_file():
                continue
            if not os.access(path, os.R_OK):
                continue

            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            if pattern.search(content):
                relative_path = path.relative_to(search_dir).as_posix()
                matches.append(relative_path)

        return {"files": matches}
    except Exception as exc:
        return error_response(f"Internal server error: {exc}", 500)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)