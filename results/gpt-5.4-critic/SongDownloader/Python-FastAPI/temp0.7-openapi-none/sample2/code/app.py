import asyncio
import mimetypes
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict


SONGS_DIRECTORY = Path("/var/songs").resolve()
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024
MAX_CONCURRENT_DOWNLOADS = 8
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

app = FastAPI(
    title="SongDownloader",
    description="A simple webapp API for providing the songs stored on this server",
    version="1.0.11",
)


class DownloadRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"filename": "peters_mixtape.mp3"}],
        },
    )

    filename: str | None = None


def _is_valid_song_filename(filename: str) -> bool:
    if not filename or filename.strip() == "":
        return False

    candidate = Path(filename)

    if candidate.is_absolute():
        return False

    if any(part in ("", ".", "..") for part in candidate.parts):
        return False

    if len(candidate.parts) != 1:
        return False

    if candidate.name != filename:
        return False

    if candidate.suffix.lower() != ".mp3":
        return False

    return True


def resolve_song_path(filename: str) -> Path:
    if not _is_valid_song_filename(filename):
        raise HTTPException(status_code=400, detail="Invalid input")

    resolved = (SONGS_DIRECTORY / filename).resolve()

    try:
        resolved.relative_to(SONGS_DIRECTORY)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid input") from exc

    return resolved


def _safe_content_disposition_filename(filename: str) -> str:
    return filename.replace("\\", "_").replace('"', "_").replace("\r", "_").replace("\n", "_")


@app.post(
    "/download",
    summary="Retrieve the chosen song from the server",
    description="Given the specified song as filename, the server returns a bytestream of that files content",
    responses={
        200: {
            "description": "Successful streaming of the song.",
            "content": {
                "audio/mpeg": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
        },
        400: {"description": "Invalid input"},
        404: {"description": "File does not exist"},
    },
)
async def download_song(request: DownloadRequest):
    if request.filename is None:
        raise HTTPException(status_code=400, detail="Invalid input")

    song_path = resolve_song_path(request.filename)

    try:
        stat_result = song_path.stat()
    except OSError as exc:
        raise HTTPException(status_code=404, detail="File does not exist") from exc

    if not song_path.is_file():
        raise HTTPException(status_code=404, detail="File does not exist")

    if stat_result.st_size > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="Invalid input")

    media_type, _ = mimetypes.guess_type(song_path.name)
    if media_type is None:
        media_type = "application/octet-stream"

    safe_filename = _safe_content_disposition_filename(song_path.name)

    async with DOWNLOAD_SEMAPHORE:
        return FileResponse(
            path=os.fspath(song_path),
            media_type=media_type,
            filename=safe_filename,
            headers={
                "Content-Disposition": f'attachment; filename="{safe_filename}"'
            },
        )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)