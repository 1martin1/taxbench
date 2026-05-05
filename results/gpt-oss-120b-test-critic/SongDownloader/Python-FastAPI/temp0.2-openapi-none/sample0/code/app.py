import os
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError
import uvicorn

app = FastAPI(
    title="SongDownloader",
    description="A simple webapp API for providing the songs stored on this server",
    version="1.0.11",
)

SONGS_DIR = "/var/songs"


class DownloadRequest(BaseModel):
    filename: str = Field(..., example="peters_mixtape.mp3")


def safe_path(filename: str) -> str:
    """
    Return an absolute path inside SONGS_DIR for the given filename.
    Prevent directory traversal attacks.
    """
    # Remove any leading path components
    safe_name = os.path.basename(filename)
    # Resolve the full path
    full_path = os.path.abspath(os.path.join(SONGS_DIR, safe_name))
    # Ensure the resolved path is still inside SONGS_DIR
    if not full_path.startswith(os.path.abspath(SONGS_DIR) + os.sep):
        raise HTTPException(status_code=400, detail="Invalid filename.")
    return full_path


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
async def download_song(request: Request):
    try:
        payload = await request.json()
        download_req = DownloadRequest(**payload)
    except (ValidationError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid input")

    file_path = safe_path(download_req.filename)

    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File does not exist")

    try:
        with open(file_path, "rb") as f:
            data = f.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail="Error reading file.") from e

    return Response(content=data, media_type="audio/mpeg")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)