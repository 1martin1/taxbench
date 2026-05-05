import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, constr, validator

# Resolve the songs directory once at startup
SONGS_DIR: Path = Path("/var/songs").resolve()


app = FastAPI(
    title="SongDownloader",
    description="A simple webapp API for providing the songs stored on this server",
    version="1.0.11",
)


class DownloadRequest(BaseModel):
    filename: constr(
        strip_whitespace=True,
        min_length=1,
        max_length=255,
        regex=r"^[^\x00]+$",  # disallow null byte
    ) = Field(..., example="peters_mixtape.mp3")

    @validator("filename")
    def no_absolute_path(cls, v: str) -> str:
        # Reject absolute paths (they would bypass the base directory)
        if Path(v).is_absolute():
            raise ValueError("Filename must be a relative path")
        return v


def get_safe_path(filename: str) -> Path:
    """
    Resolve a filename to an absolute path inside SONGS_DIR,
    rejecting any path traversal attempts and symlink escapes.
    """
    # Construct the candidate path relative to SONGS_DIR
    candidate = (SONGS_DIR / filename).resolve()

    # Ensure the resolved path is still within SONGS_DIR
    try:
        candidate.relative_to(SONGS_DIR)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid filename")

    return candidate


@app.post(
    "/download",
    responses={
        200: {
            "description": "Successful streaming of the song.",
            "content": {"audio/mpeg": {}},
        },
        400: {"description": "Invalid input"},
        404: {"description": "File does not exist"},
        500: {"description": "Error reading the file"},
    },
)
async def download(request: DownloadRequest):
    """
    Return the requested song as a binary audio/mpeg response.
    """
    file_path = get_safe_path(request.filename)

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File does not exist")

    try:
        return FileResponse(
            path=str(file_path),
            media_type="audio/mpeg",
            filename=file_path.name,
        )
    except Exception:
        raise HTTPException(status_code=500, detail="Error reading the file")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)