import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, validator

# Directory where songs are stored
SONGS_DIR = "/var/songs"

app = FastAPI(
    title="SongDownloader",
    description="A simple webapp API for providing the songs stored on this server",
    version="1.0.11",
)


class DownloadRequest(BaseModel):
    filename: str = Field(..., example="peters_mixtape.mp3")

    @validator("filename")
    def validate_filename(cls, v: str) -> str:
        # Disallow path traversal characters
        if "/" in v or "\\" in v or ".." in v:
            raise ValueError("Invalid filename")
        return v


@app.post(
    "/download",
    responses={
        200: {
            "description": "Successful streaming of the song.",
            "content": {"audio/mpeg": {"schema": {"type": "string", "format": "binary"}}},
        },
        404: {"description": "File does not exist"},
        400: {"description": "Invalid input"},
    },
)
async def download(request: DownloadRequest):
    """
    Retrieve the chosen song from the server.
    """
    file_path = os.path.join(SONGS_DIR, request.filename)

    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File does not exist")

    try:
        with open(file_path, "rb") as f:
            data = f.read()
    except Exception:
        raise HTTPException(status_code=500, detail="Error reading file")

    return Response(content=data, media_type="audio/mpeg")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)