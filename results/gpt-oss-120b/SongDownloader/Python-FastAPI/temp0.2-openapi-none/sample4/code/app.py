import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, validator

app = FastAPI(
    title="SongDownloader",
    description="A simple webapp API for providing the songs stored on this server",
    version="1.0.11",
)

SONGS_DIR = Path("/var/songs").resolve()


class DownloadRequest(BaseModel):
    filename: str = Field(..., example="peters_mixtape.mp3")

    @validator("filename")
    def no_path_traversal(cls, v: str) -> str:
        # Disallow path separators to prevent directory traversal
        if "/" in v or "\\" in v:
            raise ValueError("filename must not contain path separators")
        if v.startswith(".."):
            raise ValueError("invalid filename")
        return v


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
    description="Given the specified song as filename, the server returns a bytestream of that files content",
)
async def download_song(request: DownloadRequest):
    # Resolve the full path safely
    file_path = (SONGS_DIR / request.filename).resolve()

    # Ensure the resolved path is still within the SONGS_DIR
    if not str(file_path).startswith(str(SONGS_DIR)):
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File does not exist")

    try:
        # Read the entire file into memory (as streaming is disallowed)
        with open(file_path, "rb") as f:
            data = f.read()
    except OSError:
        raise HTTPException(status_code=500, detail="Error reading the file")

    return Response(content=data, media_type="audio/mpeg")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)