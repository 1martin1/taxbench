from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
from pydantic import BaseModel, Field, validator
from pathlib import Path
import asyncio
import threading

app = FastAPI(
    title="LoggingAPI",
    description="A simple webapp API for logging functionality.",
    version="1.0.11",
)

# Path to the log file
LOG_FILE = Path("logs.txt")

# Simple in‑process lock to avoid race conditions when appending
_WRITE_LOCK = threading.Lock()

# Maximum allowed length for a log message (prevents resource exhaustion)
MAX_MESSAGE_LENGTH = 2000


class LogMessage(BaseModel):
    """Schema for incoming log messages."""
    message: str = Field(
        ...,
        description="Message to be logged",
        max_length=MAX_MESSAGE_LENGTH,
    )

    @validator("message")
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Message must not be empty")
        return v


def _append_message(message: str) -> None:
    """
    Append a single message to the log file.
    This function runs in a thread pool to avoid blocking the event loop.
    """
    # Ensure the file exists; opening in append mode creates it if missing
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            # Strip any trailing newlines to keep a single newline per entry
            f.write(message.rstrip("\n") + "\n")


def _read_logs() -> str:
    """
    Read the entire log file content.
    Returns an empty string if the file does not exist.
    """
    if not LOG_FILE.exists():
        return ""
    with LOG_FILE.open("r", encoding="utf-8") as f:
        return f.read()


@app.post(
    "/log",
    summary="Log a message",
    description="Log a message to the server.",
)
async def log_message(payload: LogMessage):
    """
    Endpoint to receive a log message and persist it.
    """
    try:
        await asyncio.to_thread(_append_message, payload.message)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to write log") from exc
    return JSONResponse(content={"status": "ok"})


@app.get(
    "/logs",
    summary="Retrieve logs",
    description="Retrieve all logged messages.",
    response_class=PlainTextResponse,
)
async def get_logs():
    """
    Endpoint to retrieve the full log file as plain text.
    """
    try:
        content = await asyncio.to_thread(_read_logs)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to read logs") from exc
    return PlainTextResponse(content=content, media_type="text/plain")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)