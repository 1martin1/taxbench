from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field, validator
from pathlib import Path
import uvicorn

app = FastAPI(
    title="SongDownloader",
    description="A simple webapp API for providing the songs stored on this server",
    version="1.0.11",
)

# Base directory that contains the songs – resolved once at startup
SONGS_DIR = Path("/var/songs").resolve()

# Maximum allowed filename length (in characters) to avoid abuse
MAX_FILENAME_LENGTH = 255

# Optional safety limit for the size of a song that can be served (e.g., 100 MiB)
MAX_SONG_SIZE = 100 * 1024 * 1024  # 100 MiB


class DownloadRequest(BaseModel):
    filename: str = Field(..., example="peters_mixtape.mp3")

    @validator("filename")
    def filename_must_be_valid(cls, v: str) -> str:
        # Basic non‑empty check
        if not v or not v.strip():
            raise ValueError("filename must be a non‑empty string")
        # Length guard
        if len(v) > MAX_FILENAME_LENGTH:
            raise ValueError(f"filename exceeds maximum length of {MAX_FILENAME_LENGTH}")
        # Disallow any path traversal or separator characters
        if ".." in v or "/" in v or "\\" in v:
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
    response_class=Response,
    summary="Retrieve the chosen song from the server",
    description="Given the specified song as filename, the server returns a bytestream of that files content",
)
async def download(request: DownloadRequest):
    """
    Return the requested song as a binary response.
    """
    # Resolve the requested file path safely
    file_path = (SONGS_DIR / request.filename).resolve()

    # Verify the resolved path is still within the allowed directory
    try:
        file_path.relative_to(SONGS_DIR)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid filename path")

    # Ensure the target exists and is a regular file
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File does not exist")

    # Enforce a maximum file size to protect memory usage
    try:
        file_size = file_path.stat().st_size
    except OSError:
        raise HTTPException(status_code=404, detail="Unable to access file")
    if file_size > MAX_SONG_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File size exceeds allowed limit of {MAX_SONG_SIZE // (1024 * 1024)} MiB",
        )

    # Read the file content; any I/O error is treated as a not‑found situation
    try:
        content = file_path.read_bytes()
    except OSError:
        raise HTTPException(status_code=404, detail="Unable to read file")

    return Response(content=content, media_type="audio/mpeg")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)