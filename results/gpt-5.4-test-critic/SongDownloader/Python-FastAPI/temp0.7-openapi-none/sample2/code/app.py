import mimetypes
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field


SONGS_DIRECTORY = Path("/var/songs").resolve()

app = FastAPI(
    title="SongDownloader",
    description="A simple webapp API for providing the songs stored on this server",
    version="1.0.11",
)


class DownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(..., examples=["peters_mixtape.mp3"])


@app.post(
    "/download",
    summary="Retrieve the chosen song from the server",
    description=(
        "Given the specified song as filename, the server returns a bytestream "
        "of that files content"
    ),
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
    filename = request.filename.strip()

    if not filename:
        raise HTTPException(status_code=400, detail="Invalid input")

    requested_path = (SONGS_DIRECTORY / filename).resolve()

    try:
        requested_path.relative_to(SONGS_DIRECTORY)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid input")

    if not requested_path.is_file():
        raise HTTPException(status_code=404, detail="File does not exist")

    try:
        content = requested_path.read_bytes()
    except OSError:
        raise HTTPException(status_code=404, detail="File does not exist")

    media_type, _ = mimetypes.guess_type(str(requested_path))
    if media_type is None:
        media_type = "audio/mpeg"

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{requested_path.name}"'
        },
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)