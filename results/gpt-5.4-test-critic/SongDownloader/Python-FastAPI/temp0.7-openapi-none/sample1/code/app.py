import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict


SONGS_DIRECTORY = Path("/var/songs").resolve()

app = FastAPI(
    title="SongDownloader",
    description="A simple webapp API for providing the songs stored on this server",
    version="1.0.11",
)


class DownloadRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "filename": "peters_mixtape.mp3"
            }
        }
    )

    filename: str


def resolve_song_path(filename: str) -> Path:
    if not filename or filename.strip() == "":
        raise HTTPException(status_code=400, detail="Invalid input")

    if os.path.basename(filename) != filename:
        raise HTTPException(status_code=400, detail="Invalid input")

    song_path = (SONGS_DIRECTORY / filename).resolve()

    try:
        song_path.relative_to(SONGS_DIRECTORY)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid input")

    return song_path


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
    song_path = resolve_song_path(request.filename)

    if not song_path.exists() or not song_path.is_file():
        raise HTTPException(status_code=404, detail="File does not exist")

    try:
        with song_path.open("rb") as song_file:
            content = song_file.read()
    except OSError:
        raise HTTPException(status_code=404, detail="File does not exist")

    return Response(
        content=content,
        media_type="audio/mpeg",
        headers={
            "Content-Disposition": f'attachment; filename="{song_path.name}"'
        },
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)