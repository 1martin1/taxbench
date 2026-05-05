from pathlib import Path
from typing import Dict

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, field_validator
import uvicorn

APP_TITLE = "SongDownloader"
APP_DESCRIPTION = "A simple webapp API for providing the songs stored on this server"
APP_VERSION = "1.0.11"

SONGS_DIR = Path("/var/songs").resolve()


class DownloadRequest(BaseModel):
    filename: str

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, v: str) -> str:
        if not isinstance(v, str):
            raise ValueError("Invalid filename")
        v = v.strip()
        if not v:
            raise ValueError("Invalid filename")
        # Disallow path separators and null bytes
        if any(sep in v for sep in ("/", "\\")) or "\x00" in v:
            raise ValueError("Invalid filename")
        # Disallow reserved names
        if v in (".", ".."):
            raise ValueError("Invalid filename")
        return v


app = FastAPI(title=APP_TITLE, description=APP_DESCRIPTION, version=APP_VERSION)


def resolve_song_path(filename: str) -> Path:
    candidate = (SONGS_DIR / filename).resolve()
    try:
        # Ensure the resolved path is within the SONGS_DIR
        candidate.relative_to(SONGS_DIR)
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
                    "schema": {
                        "type": "string",
                        "format": "binary",
                    }
                }
            },
        },
        404: {"description": "File does not exist"},
        400: {"description": "Invalid input"},
    },
)
async def download_song(payload: DownloadRequest):
    file_path = resolve_song_path(payload.filename)

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File does not exist")

    try:
        data = file_path.read_bytes()
    except FileNotFoundError:
        # Race condition: file removed after existence check
        raise HTTPException(status_code=404, detail="File does not exist")
    except Exception:
        # Any other read error is treated as invalid input for this simple API
        raise HTTPException(status_code=400, detail="Invalid input")

    headers: Dict[str, str] = {
        "Content-Disposition": f'attachment; filename="{payload.filename}"',
        "Content-Length": str(len(data)),
    }
    return Response(content=data, media_type="audio/mpeg", headers=headers)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)