from pathlib import Path
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator

DATA_DIR = Path("/data").resolve()

app = FastAPI(
    title="FileSearch",
    description="An API for checking if a file with given content or name exists on the server",
    version="1.0.11",
)


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_content: Optional[str] = None
    search_filename: Optional[str] = None
    search_dir: Optional[str] = None

    @field_validator("search_dir")
    @classmethod
    def validate_search_dir(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if not value.strip():
            raise ValueError("search_dir must not be empty")
        return value

    @field_validator("search_content", "search_filename")
    @classmethod
    def validate_optional_strings(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value == "":
            raise ValueError("search fields must not be empty strings")
        return value


class SearchResponse(BaseModel):
    files: List[str]


def resolve_search_directory(search_dir: Optional[str]) -> Path:
    if search_dir is None:
        return DATA_DIR

    normalized = search_dir.lstrip("/")
    candidate = (DATA_DIR / normalized).resolve()

    try:
        candidate.relative_to(DATA_DIR)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="search_dir must be within /data") from exc

    if not candidate.exists():
        raise HTTPException(status_code=400, detail="search_dir does not exist")
    if not candidate.is_dir():
        raise HTTPException(status_code=400, detail="search_dir is not a directory")

    return candidate


def file_matches(file_path: Path, search_content: Optional[str], search_filename: Optional[str]) -> bool:
    name_match = False
    content_match = False

    if search_filename is not None:
        name_match = file_path.name == search_filename

    if search_content is not None:
        try:
            with file_path.open("r", encoding="utf-8", errors="ignore") as f:
                for chunk in iter(lambda: f.read(8192), ""):
                    if search_content in chunk:
                        content_match = True
                        break
        except (OSError, UnicodeError):
            content_match = False

    return name_match or content_match


@app.post(
    "/search",
    response_model=SearchResponse,
    responses={
        200: {
            "description": "List of files that contain the specified content or match the filename."
        },
        400: {"description": "Invalid input"},
    },
    summary="Retreive a list of files from the server that contain the specified content or match the filename.",
    description=(
        "The server stores files in the /data directory. This endpoint will return a list "
        "of complete paths to files in data that contain the specified content or start. "
        "It is possible to specify a relative path in search_dir."
    ),
)
def search_files(request: SearchRequest) -> SearchResponse:
    if request.search_content is None and request.search_filename is None:
        raise HTTPException(
            status_code=400,
            detail="At least one of search_content or search_filename must be provided",
        )

    search_root = resolve_search_directory(request.search_dir)
    matched_files: List[str] = []

    for path in search_root.rglob("*"):
        if not path.is_file():
            continue
        if file_matches(path, request.search_content, request.search_filename):
            matched_files.append(str(path.resolve()))

    matched_files.sort()
    return SearchResponse(files=matched_files)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)