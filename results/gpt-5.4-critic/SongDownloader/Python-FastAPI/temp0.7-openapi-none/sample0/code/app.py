import os
import re
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

SONGS_DIRECTORY = Path("/var/songs").resolve()
ALLOWED_EXTENSION = ".mp3"
SAFE_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9._ -]+\.mp3$", re.IGNORECASE)

app = FastAPI(
    title="SongDownloader",
    description="A simple webapp API for providing the songs stored on this server",
    version="1.0.11",
)


class DownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: Optional[str] = None


def _validate_filename(filename: Optional[str]) -> Path:
    if filename is None or not isinstance(filename, str):
        raise HTTPException(status_code=400, detail="Invalid input")

    filename = filename.strip()
    if not filename:
        raise HTTPException(status_code=400, detail="Invalid input")

    if os.path.sep in filename or (os.path.altsep and os.path.altsep in filename):
        raise HTTPException(status_code=400, detail="Invalid input")

    if not filename.lower().endswith(ALLOWED_EXTENSION):
        raise HTTPException(status_code=400, detail="Invalid input")

    if not SAFE_FILENAME_PATTERN.fullmatch(filename):
        raise HTTPException(status_code=400, detail="Invalid input")

    requested_path = (SONGS_DIRECTORY / filename).resolve()

    try:
        requested_path.relative_to(SONGS_DIRECTORY)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid input")

    if not requested_path.is_file():
        raise HTTPException(status_code=404, detail="File does not exist")

    return requested_path


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
def download_song(request: DownloadRequest):
    song_path = _validate_filename(request.filename)

    try:
        return FileResponse(
            path=song_path,
            media_type="audio/mpeg",
            filename=song_path.name,
        )
    except OSError:
        raise HTTPException(status_code=404, detail="File does not exist")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)