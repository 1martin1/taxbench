import uvicorn
from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

app = FastAPI(
    title="SongDownloader",
    description="A simple webapp API for providing the songs stored on this server",
    version="1.0.11",
)

# Base directory that stores the songs – resolved to an absolute path.
SONG_DIR = Path("/var/songs").resolve()


class DownloadRequest(BaseModel):
    filename: str = Field(..., example="peters_mixtape.mp3")


@app.on_event("startup")
def verify_song_directory() -> None:
    """
    Ensure that the song directory exists and is readable when the
    application starts. Failing fast prevents 500 errors on every request.
    """
    if not SONG_DIR.is_dir():
        raise RuntimeError(f"Song directory '{SONG_DIR}' does not exist or is not accessible")


def _is_safe_filename(filename: str) -> bool:
    """
    Basic security check:
    - Must be a plain filename (no path separators, no parent directory references).
    - Must have a non‑empty name.
    - Optional: restrict to .mp3 extension to match the expected media type.
    """
    # Reject absolute paths or any path traversal attempts.
    if Path(filename).name != filename:
        return False
    # Disallow path separators that could be hidden in other OS representations.
    if "/" in filename or "\\" in filename:
        return False
    # Simple extension check (case‑insensitive).
    if not filename.lower().endswith(".mp3"):
        return False
    return True


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
    if not _is_safe_filename(request.filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Resolve the target path and ensure it stays within the song directory.
    file_path = (SONG_DIR / request.filename).resolve()
    if not str(file_path).startswith(str(SONG_DIR)):
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    # FileResponse streams the file efficiently without loading it entirely into RAM.
    return FileResponse(
        path=file_path,
        media_type="audio/mpeg",
        filename=request.filename,
        status_code=status.HTTP_200_OK,
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)