import os
from pathlib import Path
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field


DATA_DIR = Path("/data").resolve()


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_content: Optional[str] = Field(default=None, example="Hello World")
    search_filename: Optional[str] = Field(default=None, example="file.txt")
    search_dir: Optional[str] = Field(default=None, example="/songs")


class SearchResponse(BaseModel):
    files: List[str] = Field(
        ...,
        example=["/data/files/file1.txt", "/data/songs/song.mp3"],
    )


app = FastAPI(
    title="FileSearch",
    description="An API for checking if a file with given content or name exists on the server",
    version="1.0.11",
)


def resolve_search_directory(search_dir: Optional[str]) -> Path:
    if not search_dir:
        return DATA_DIR

    normalized = search_dir.lstrip("/")
    candidate = (DATA_DIR / normalized).resolve()

    try:
        candidate.relative_to(DATA_DIR)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="search_dir must be within /data") from exc

    if not candidate.exists() or not candidate.is_dir():
        raise HTTPException(status_code=400, detail="search_dir does not exist or is not a directory")

    return candidate


def file_matches_content(file_path: Path, search_content: str) -> bool:
    try:
        with file_path.open("r", encoding="utf-8", errors="ignore") as file_handle:
            for chunk in iter(lambda: file_handle.read(8192), ""):
                if search_content in chunk:
                    return True
    except (OSError, UnicodeError):
        return False
    return False


@app.post("/search", response_model=SearchResponse)
def search_files(payload: SearchRequest) -> SearchResponse:
    if payload.search_content is None and payload.search_filename is None:
        raise HTTPException(
            status_code=400,
            detail="At least one of search_content or search_filename must be provided",
        )

    base_dir = resolve_search_directory(payload.search_dir)
    matched_files: List[str] = []

    for root, _, filenames in os.walk(base_dir):
        root_path = Path(root)

        for filename in filenames:
            file_path = root_path / filename
            matches = False

            if payload.search_filename is not None and filename == payload.search_filename:
                matches = True

            if not matches and payload.search_content is not None:
                if file_matches_content(file_path, payload.search_content):
                    matches = True

            if matches:
                matched_files.append(str(file_path.resolve()))

    return SearchResponse(files=matched_files)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)