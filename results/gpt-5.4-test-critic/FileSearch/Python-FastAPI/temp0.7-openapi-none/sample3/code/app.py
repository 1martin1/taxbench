from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

DATA_ROOT = Path("/data").resolve()

app = FastAPI(
    title="FileSearch",
    description="An API for checking if a file with given content or name exists on the server",
    version="1.0.11",
)


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_content: Optional[str] = Field(default=None, example="Hello World")
    search_filename: Optional[str] = Field(default=None, example="file.txt")
    search_dir: Optional[str] = Field(default=None, example="/songs")

    @model_validator(mode="after")
    def validate_search_criteria(self) -> "SearchRequest":
        if not self.search_content and not self.search_filename:
            raise ValueError("At least one of search_content or search_filename must be provided.")
        return self


class SearchResponse(BaseModel):
    files: list[str] = Field(
        ...,
        example=["/data/files/file1.txt", "/data/songs/song.mp3"],
    )


def resolve_search_directory(search_dir: Optional[str]) -> Path:
    if search_dir is None or search_dir == "":
        target = DATA_ROOT
    else:
        normalized = search_dir.lstrip("/")
        target = (DATA_ROOT / normalized).resolve()

    try:
        target.relative_to(DATA_ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="search_dir must stay within /data") from exc

    if not target.exists():
        raise HTTPException(status_code=400, detail="search_dir does not exist")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="search_dir is not a directory")

    return target


def file_contains_text(file_path: Path, needle: str) -> bool:
    try:
        with file_path.open("r", encoding="utf-8", errors="ignore") as handle:
            for chunk in iter(lambda: handle.read(8192), ""):
                if needle in chunk:
                    return True
    except (OSError, UnicodeError):
        return False
    return False


@app.post("/search", response_model=SearchResponse)
def search_files(payload: SearchRequest) -> SearchResponse:
    search_root = resolve_search_directory(payload.search_dir)
    matched_files: list[str] = []

    for root, _, files in os.walk(search_root):
        root_path = Path(root)

        for file_name in files:
            file_path = root_path / file_name

            filename_match = False
            content_match = False

            if payload.search_filename is not None:
                filename_match = file_name == payload.search_filename

            if payload.search_content is not None and file_path.is_file():
                content_match = file_contains_text(file_path, payload.search_content)

            if filename_match or content_match:
                matched_files.append(str(file_path.resolve()))

    matched_files.sort()
    return SearchResponse(files=matched_files)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)