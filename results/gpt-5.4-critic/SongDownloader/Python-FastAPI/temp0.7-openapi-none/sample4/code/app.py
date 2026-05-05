import asyncio
import os
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict


SONGS_DIRECTORY = Path("/var/songs").resolve()
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024
MAX_CONCURRENT_DOWNLOADS = 8


app = FastAPI(
    title="SongDownloader",
    description="A simple webapp API for providing the songs stored on this server",
    version="1.0.11",
)

_download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)


class DownloadRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "filename": "peters_mixtape.mp3"
            }
        }
    )

    filename: Optional[str] = None


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> Response:
    return Response(content="Invalid input", status_code=400, media_type="text/plain")


def _sanitize_filename(filename: str) -> str:
    sanitized = filename.replace("\r", "").replace("\n", "").replace('"', "")
    return sanitized


def _validate_filename(filename: Optional[str]) -> str:
    if filename is None:
        raise HTTPException(status_code=400, detail="Invalid input")

    if not isinstance(filename, str):
        raise HTTPException(status_code=400, detail="Invalid input")

    if filename.strip() == "":
        raise HTTPException(status_code=400, detail="Invalid input")

    if Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="Invalid input")

    if any(ch in filename for ch in ("\x00", "/", "\\")):
        raise HTTPException(status_code=400, detail="Invalid input")

    return filename


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
async def download_song(request: DownloadRequest) -> Response:
    filename = _validate_filename(request.filename)
    song_path = (SONGS_DIRECTORY / filename).resolve()

    try:
        song_path.relative_to(SONGS_DIRECTORY)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid input")

    if not song_path.exists() or not song_path.is_file():
        raise HTTPException(status_code=404, detail="File does not exist")

    try:
        file_size = song_path.stat().st_size
    except OSError:
        raise HTTPException(status_code=404, detail="File does not exist")

    if file_size > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="Invalid input")

    async with _download_semaphore:
        try:
            content = await asyncio.to_thread(song_path.read_bytes)
        except OSError:
            raise HTTPException(status_code=404, detail="File does not exist")

    safe_filename = _sanitize_filename(filename)

    return Response(
        content=content,
        media_type="audio/mpeg",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_filename}"'
        },
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)