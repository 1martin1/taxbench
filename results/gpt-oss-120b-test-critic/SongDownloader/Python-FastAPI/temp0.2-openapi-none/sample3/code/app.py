import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

# Directory where songs are stored
SONGS_DIR = "/var/songs"

class DownloadRequest(BaseModel):
    filename: str = Field(..., example="peters_mixtape.mp3")

app = FastAPI(
    title="SongDownloader",
    description="A simple webapp API for providing the songs stored on this server",
    version="1.0.11",
)

@app.post(
    "/download",
    response_class=Response,
    responses={
        200: {
            "description": "Successful streaming of the song.",
            "content": {"audio/mpeg": {"schema": {"type": "string", "format": "binary"}}},
        },
        404: {"description": "File does not exist"},
        400: {"description": "Invalid input"},
    },
)
def download_song(request: DownloadRequest):
    """
    Retrieve the chosen song from the server.
    """
    # Basic validation to prevent path traversal
    if "/" in request.filename or "\\" in request.filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Resolve the absolute path and ensure it stays within SONGS_DIR
    absolute_songs_dir = os.path.abspath(SONGS_DIR)
    file_path = os.path.abspath(os.path.join(absolute_songs_dir, request.filename))

    if not file_path.startswith(absolute_songs_dir + os.sep):
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    try:
        with open(file_path, "rb") as f:
            file_bytes = f.read()
    except Exception:
        raise HTTPException(status_code=500, detail="Error reading the file")

    return Response(content=file_bytes, media_type="audio/mpeg")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)