import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict


SONGS_DIRECTORY = Path("/var/songs").resolve()


class DownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str


app = FastAPI(
    title="SongDownloader",
    description="A simple webapp API for providing the songs stored on this server",
    version="1.0.11",
)


def _validate_filename(filename: str) -> Path:
    if not filename or filename.strip() == "":
        raise HTTPException(status_code=400, detail="Invalid input")

    if "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid input")

    candidate = (SONGS_DIRECTORY / filename).resolve()

    try:
        candidate.relative_to(SONGS_DIRECTORY)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid input")

    return candidate


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
async def download_song(request: DownloadRequest) -> Response:
    file_path = _validate_filename(request.filename)

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File does not exist")

    try:
        with open(file_path, "rb") as song_file:
            content = song_file.read()
    except OSError:
        raise HTTPException(status_code=404, detail="File does not exist")

    headers = {
        "Content-Disposition": f'attachment; filename="{file_path.name}"'
    }
    return Response(content=content, media_type="audio/mpeg", headers=headers)


if __name__ == "__main__":
    os.makedirs(SONGS_DIRECTORY, exist_ok=True)
    uvicorn.run(app, host="0.0.0.0", port=5000)