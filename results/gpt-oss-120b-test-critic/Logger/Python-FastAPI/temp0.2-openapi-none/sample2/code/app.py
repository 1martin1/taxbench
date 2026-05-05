import os
import html
from pathlib import Path
from typing import Any

import anyio
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

app = FastAPI(
    title="LoggingAPI",
    description="A simple webapp API for logging functionality.",
    version="1.0.11",
)

# Resolve log file path safely relative to the current working directory
LOG_FILE = Path(__file__).with_name("logs.txt").resolve()


class LogMessage(BaseModel):
    message: str


async def _ensure_log_file() -> None:
    """Make sure the log file exists, creating it if necessary."""
    def _create():
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOG_FILE.touch(exist_ok=True)

    await anyio.to_thread.run_sync(_create)


async def _append_message(message: str) -> None:
    """Append a single log message to the file in a thread‑pool to avoid blocking."""
    def _write():
        with LOG_FILE.open("a", encoding="utf-8") as f:
            # Strip trailing newlines to keep one entry per line
            f.write(message.rstrip("\n") + "\n")

    await anyio.to_thread.run_sync(_write)


async def _read_logs() -> str:
    """Read the entire log file content in a thread‑pool."""
    def _read() -> str:
        with LOG_FILE.open("r", encoding="utf-8") as f:
            return f.read()

    return await anyio.to_thread.run_sync(_read)


@app.post(
    "/log",
    summary="Log a message",
    description="Log a message to the server.",
)
async def log_message(payload: LogMessage) -> dict[str, Any]:
    """
    Append the received message to the log file, one line per message.
    """
    await _ensure_log_file()
    try:
        await _append_message(payload.message)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write log: {exc}") from exc
    return {"detail": "Message logged successfully"}


@app.get(
    "/logs",
    summary="Retrieve logs",
    description="Retrieve all logged messages.",
    response_class=PlainTextResponse,
)
async def get_logs() -> PlainTextResponse:
    """
    Return the entire contents of the log file as plain text.
    The content is HTML‑escaped to mitigate accidental rendering of markup.
    """
    await _ensure_log_file()
    try:
        raw_content = await _read_logs()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read logs: {exc}") from exc

    # Escape to ensure any HTML/JS characters are rendered as plain text
    escaped_content = html.escape(raw_content, quote=False)
    return PlainTextResponse(content=escaped_content, media_type="text/plain")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)