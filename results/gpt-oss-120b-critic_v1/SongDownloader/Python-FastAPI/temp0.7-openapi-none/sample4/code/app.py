import asyncio
import json
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError, validator

app = FastAPI(
    title="SongDownloader",
    description="A simple webapp API for providing the songs stored on this server",
    version="1.0.11",
)

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
BASE_SONG_DIR = Path("/var/songs").resolve()
MAX_REQUEST_BODY = 1 * 1024          # 1 KiB – enough for the JSON payload
MAX_FILE_SIZE = 20 * 1024 * 1024     # 20 MiB – limit to prevent OOM
FILENAME_REGEX = re.compile(r"^[\w\-.]+$")  # alphanumerics, underscore, dash, dot


# --------------------------------------------------------------------------- #
# Pydantic model with strict validation
# --------------------------------------------------------------------------- #
class DownloadRequest(BaseModel):
    filename: str = Field(..., example="peters_mixtape.mp3")

    @validator("filename")
    def filename_must_be_safe(cls, v: str) -> str:
        # Must match allowed pattern
        if not FILENAME_REGEX.fullmatch(v):
            raise ValueError("Invalid filename: only letters, numbers, underscore, dash and dot are allowed")
        # Length guard
        if len(v) > 255:
            raise ValueError("Filename too long")
        return v


# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #
def is_path_inside_base(path: Path, base: Path) -> bool:
    """
    Return True if *path* is inside *base* (including sub‑directories).
    Uses pathlib.Path.is_relative_to when available (Python 3.9+).
    """
    try:
        return path.is_relative_to(base)  # type: ignore[attr-defined]
    except AttributeError:
        # Fallback for older Python versions
        try:
            path.relative_to(base)
            return True
        except ValueError:
            return False


async def read_file_bytes(path: Path) -> bytes:
    """
    Read file contents using a thread pool to avoid blocking the event loop.
    """
    return await asyncio.to_thread(path.read_bytes)


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #
@app.post(
    "/download",
    responses={
        200: {
            "description": "Successful streaming of the song.",
            "content": {"audio/mpeg": {}},
        },
        400: {"description": "Invalid input"},
        404: {"description": "File does not exist"},
    },
    summary="Retrieve the chosen song from the server",
    description=(
        "Given the specified song as filename, the server returns a bytestream "
        "of that file's content."
    ),
)
async def download(request: Request):
    """
    Return the requested song as an `audio/mpeg` response.
    """
    # -------------------------- Request size guard -------------------------- #
    raw_body = await request.body()
    if len(raw_body) > MAX_REQUEST_BODY:
        raise HTTPException(status_code=400, detail="Request body too large")

    # -------------------------- Parse & validate JSON ---------------------- #
    try:
        payload: Any = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc.msg}")

    try:
        data = DownloadRequest(**payload)
    except ValidationError as exc:
        # Return first error message for simplicity
        detail = "; ".join(err["msg"] for err in exc.errors())
        raise HTTPException(status_code=400, detail=detail)

    # -------------------------- Resolve safe file path ---------------------- #
    target_path = (BASE_SONG_DIR / data.filename).resolve()

    if not is_path_inside_base(target_path, BASE_SONG_DIR):
        raise HTTPException(status_code=400, detail="Invalid file path")

    if not target_path.is_file():
        raise HTTPException(status_code=404, detail="File does not exist")

    # -------------------------- File size guard ---------------------------- #
    try:
        file_size = target_path.stat().st_size
    except OSError:
        raise HTTPException(status_code=404, detail="File does not exist")

    if file_size > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large")

    # -------------------------- Read file without blocking ----------------- #
    try:
        file_bytes = await read_file_bytes(target_path)
    except OSError:
        # Treat read errors as missing file to stay within documented responses
        raise HTTPException(status_code=404, detail="File does not exist")

    # -------------------------- Return response ----------------------------- #
    return Response(content=file_bytes, media_type="audio/mpeg")


# --------------------------------------------------------------------------- #
# Server entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)