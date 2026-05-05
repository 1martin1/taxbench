import os
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field, validator

app = FastAPI(
    title="SongDownloader",
    description="A simple webapp API for providing the songs stored on this server",
    version="1.0.11",
)

# Directory where songs are stored
SONGS_DIR = Path("/var/songs").resolve()


class DownloadRequest(BaseModel):
    filename: str = Field(..., example="peters_mixtape.mp3")

    @validator("filename")
    def filename_must_be_safe(cls, v: str) -> str:
        # Disallow path traversal characters
        if ".." in v or "/" in v or "\\" in v:
            raise ValueError("Invalid filename")
        if not v.strip():
            raise ValueError("Filename cannot be empty")
        return v


def get_song_path(filename: str) -> Path:
    """
    Resolve the absolute path of the requested song and ensure it stays
    inside the SONGS_DIR directory.
    """
    candidate = (SONGS_DIR / filename).resolve()
    if not str(candidate).startswith(str(SONGS_DIR)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid filename path",
        )
    return candidate


@app.post(
    "/download",
    responses={
        200: {
            "description": "Successful streaming of the song.",
            "content": {"audio/mpeg": {"schema": {"type": "string", "format": "binary"}}},
        },
        400: {"description": "Invalid input"},
        404: {"description": "File does not exist"},
    },
    summary="Retrieve the chosen song from the server",
    description=(
        "Given the specified song as filename, the server returns a bytestream "
        "of that file's content."
    ),
)
async def download_song(request: Request, payload: DownloadRequest) -> Response:
    """
    Return the full content of the requested song as an audio/mpeg response.
    """
    try:
        song_path = get_song_path(payload.filename)
    except HTTPException as exc:
        raise exc

    if not song_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File does not exist",
        )

    try:
        # Read the entire file into memory (as streaming responses are prohibited)
        with open(song_path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error reading file: {e}",
        )

    return Response(content=data, media_type="audio/mpeg")


if __name__ == "__main__":
    # Run the application on 0.0.0.0:5000
    uvicorn.run(app, host="0.0.0.0", port=5000)