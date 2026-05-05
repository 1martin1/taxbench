import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, ValidationError
import uvicorn

# Base directory where songs are stored
BASE_SONG_DIR = Path("/var/songs").resolve()

# Maximum allowed size for the request body (1 KiB)
MAX_BODY_SIZE = 1024


class DownloadRequest(BaseModel):
    filename: str = Field(..., example="peters_mixtape.mp3")


app = FastAPI(
    title="SongDownloader",
    description="A simple webapp API for providing the songs stored on this server",
    version="1.0.11",
)


def safe_song_path(filename: str) -> Path:
    """
    Resolve the requested filename against the base song directory and ensure
    the resulting path is still within that directory (prevents path traversal).
    """
    # Reject absolute paths or path components that attempt traversal
    if Path(filename).is_absolute() or ".." in Path(filename).parts:
        raise HTTPException(status_code=400, detail="Invalid filename")

    candidate = (BASE_SONG_DIR / filename).resolve()
    try:
        # Python 3.9+: ensure the resolved path is inside BASE_SONG_DIR
        if not candidate.is_relative_to(BASE_SONG_DIR):
            raise HTTPException(status_code=400, detail="Invalid filename")
    except AttributeError:
        # Fallback for older Python versions
        if not str(candidate).startswith(str(BASE_SONG_DIR)):
            raise HTTPException(status_code=400, detail="Invalid filename")
    return candidate


@app.post(
    "/download",
    responses={
        200: {
            "description": "Successful streaming of the song.",
            "content": {"audio/mpeg": {"schema": {"type": "string", "format": "binary"}}},
        },
        404: {"description": "File does not exist"},
        400: {"description": "Invalid input"},
        413: {"description": "Payload too large"},
        500: {"description": "Internal server error"},
    },
)
async def download(request: Request) -> Response:
    """
    Retrieve the chosen song from the server.
    """
    raw_body = await request.body()
    if len(raw_body) > MAX_BODY_SIZE:
        raise HTTPException(status_code=413, detail="Payload too large")

    try:
        body: Any = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    try:
        download_req = DownloadRequest(**body)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail="Invalid input data") from exc

    song_path = safe_song_path(download_req.filename)

    if not song_path.is_file():
        raise HTTPException(status_code=404, detail="File does not exist")

    try:
        # Use FileResponse which efficiently serves files without loading them fully into memory.
        return FileResponse(
            path=song_path,
            media_type="audio/mpeg",
            filename=song_path.name,
        )
    except OSError:
        raise HTTPException(status_code=500, detail="Error reading the file")


if __name__ == "__main__":
    # Run the application on 0.0.0.0:5000
    uvicorn.run(app, host="0.0.0.0", port=5000)