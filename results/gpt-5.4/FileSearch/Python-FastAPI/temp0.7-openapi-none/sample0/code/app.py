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
        if value.strip() == "":
            return None
        return value

    @field_validator("search_content", "search_filename")
    @classmethod
    def normalize_empty_strings(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if value == "":
            return None
        return value


class SearchResponse(BaseModel):
    files: List[str]


def resolve_search_root(search_dir: Optional[str]) -> Path:
    if search_dir is None:
        return DATA_DIR

    candidate = Path(search_dir)
    if candidate.is_absolute():
        relative_part = str(candidate).lstrip("/")
        resolved = (DATA_DIR / relative_part).resolve()
    else:
        resolved = (DATA_DIR / candidate).resolve()

    try:
        resolved.relative_to(DATA_DIR)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="search_dir must stay within /data") from exc

    if not resolved.exists() or not resolved.is_dir():
        raise HTTPException(status_code=400, detail="search_dir does not exist or is not a directory")

    return resolved


def file_matches_content(file_path: Path, search_content: str) -> bool:
    try:
        with file_path.open("r", encoding="utf-8", errors="ignore") as file:
            for chunk in iter(lambda: file.read(8192), ""):
                if search_content in chunk:
                    return True
    except (OSError, UnicodeError):
        return False
    return False


@app.post("/search", response_model=SearchResponse)
def search_files(request: SearchRequest) -> SearchResponse:
    if request.search_content is None and request.search_filename is None:
        raise HTTPException(
            status_code=400,
            detail="At least one of search_content or search_filename must be provided",
        )

    search_root = resolve_search_root(request.search_dir)
    matched_files: List[str] = []

    try:
        for path in search_root.rglob("*"):
            if not path.is_file():
                continue

            filename_match = (
                request.search_filename is not None and path.name == request.search_filename
            )
            content_match = (
                request.search_content is not None and file_matches_content(path, request.search_content)
            )

            if filename_match or content_match:
                matched_files.append(str(path.resolve()))
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Failed to search files: {exc}") from exc

    matched_files.sort()
    return SearchResponse(files=matched_files)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)