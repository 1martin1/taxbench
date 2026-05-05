import os
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict


SONGS_DIRECTORY = Path("/var/songs").resolve()
MAX_FILENAME_LENGTH = 255
CHUNK_SIZE = 64 * 1024


class DownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str | None = None


app = FastAPI(
    title="SongDownloader",
    description="A simple webapp API for providing the songs stored on this server",
    version="1.0.11",
)


def error_response(status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": "Invalid input"})


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return error_response(400)


def validate_filename(filename: Any) -> str | None:
    if filename is None:
        return None

    if not isinstance(filename, str):
        return None

    if not filename or filename.strip() == "":
        return None

    if len(filename) > MAX_FILENAME_LENGTH:
        return None

    if os.path.basename(filename) != filename:
        return None

    if "\x00" in filename:
        return None

    return filename


def resolve_song_path(filename: str) -> Path | None:
    song_path = (SONGS_DIRECTORY / filename).resolve()

    try:
        song_path.relative_to(SONGS_DIRECTORY)
    except ValueError:
        return None

    return song_path


def iter_file_chunks(file_path: Path):
    with file_path.open("rb") as song_file:
        while True:
            chunk = song_file.read(CHUNK_SIZE)
            if not chunk:
                break
            yield chunk


@app.post(
    "/download",
    summary="Retrieve the chosen song from the server",
    description="Given the specified song as filename, the server returns a bytestream of that files content",
    responses={
        200: {
            "description": "Successful streaming of the song.",
            "content": {
                "audio/mpeg": {
                    "schema": {
                        "type": "string",
                        "format": "binary",
                    }
                }
            },
        },
        400: {"description": "Invalid input"},
        404: {"description": "File does not exist"},
    },
)
def download_song(request: DownloadRequest):
    filename = validate_filename(request.filename)
    if filename is None:
        return error_response(400)

    song_path = resolve_song_path(filename)
    if song_path is None:
        return error_response(400)

    try:
        if not song_path.exists() or not song_path.is_file():
            return error_response(404)

        file_stat = song_path.stat()
    except OSError:
        return error_response(404)

    return StreamingResponse(
        iter_file_chunks(song_path),
        media_type="audio/mpeg",
        headers={
            "Content-Disposition": f'attachment; filename="{song_path.name}"',
            "Content-Length": str(file_stat.st_size),
        },
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)