import os
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

SONGS_DIRECTORY = Path("/var/songs").resolve()
MAX_FILENAME_LENGTH = 255

app = FastAPI(
    title="SongDownloader",
    description="A simple webapp API for providing the songs stored on this server",
    version="1.0.11",
)


class DownloadRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "filename": "peters_mixtape.mp3"
            }
        },
    )

    filename: str | None = Field(default=None, max_length=MAX_FILENAME_LENGTH)

    @field_validator("filename")
    @classmethod
    def validate_filename_length(cls, value: str | None) -> str | None:
        if value is not None and len(value) > MAX_FILENAME_LENGTH:
            raise ValueError("Invalid input")
        return value


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": "Invalid input"})


def resolve_song_path(filename: Any) -> Path:
    if not isinstance(filename, str):
        raise HTTPException(status_code=400, detail="Invalid input")

    if filename == "" or filename.strip() == "":
        raise HTTPException(status_code=400, detail="Invalid input")

    if len(filename) > MAX_FILENAME_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid input")

    if os.path.sep in filename or (os.path.altsep and os.path.altsep in filename):
        raise HTTPException(status_code=400, detail="Invalid input")

    candidate = (SONGS_DIRECTORY / filename).resolve()

    try:
        candidate.relative_to(SONGS_DIRECTORY)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid input")

    return candidate


@app.post(
    "/download",
    responses={
        200: {
            "description": "Successful streaming of the song.",
            "content": {
                "audio/mpeg": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
        },
        404: {"description": "File does not exist"},
        400: {"description": "Invalid input"},
    },
    summary="Retrieve the chosen song from the server",
    description="Given the specified song as filename, the server returns a bytestream of that files content",
)
async def download_song(request: DownloadRequest) -> FileResponse:
    song_path = resolve_song_path(request.filename)

    if not song_path.exists() or not song_path.is_file():
        raise HTTPException(status_code=404, detail="File does not exist")

    try:
        stat_result = song_path.stat()
        if stat_result.st_size < 0:
            raise HTTPException(status_code=404, detail="File does not exist")
    except OSError:
        raise HTTPException(status_code=404, detail="File does not exist")

    return FileResponse(
        path=song_path,
        media_type="audio/mpeg",
        filename=song_path.name,
        headers={
            "Content-Disposition": f'attachment; filename="{song_path.name}"'
        },
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)