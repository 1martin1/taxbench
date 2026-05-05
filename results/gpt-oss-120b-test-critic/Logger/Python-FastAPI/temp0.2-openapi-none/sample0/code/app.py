import os
from pathlib import Path
from typing import Final

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field, validator
import uvicorn

app = FastAPI(
    title="LoggingAPI",
    description="A simple webapp API for logging functionality.",
    version="1.0.11",
)

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
LOG_FILE: Final[Path] = Path("logs.txt")
MAX_MESSAGE_LENGTH: Final[int] = 2048          # characters
MAX_LOG_FILE_SIZE: Final[int] = 5 * 1024 * 1024  # 5 MiB


# --------------------------------------------------------------------------- #
# Pydantic model with validation
# --------------------------------------------------------------------------- #
class LogMessage(BaseModel):
    message: str = Field(..., max_length=MAX_MESSAGE_LENGTH)

    @validator("message")
    def strip_newlines(cls, v: str) -> str:
        """Remove trailing newlines to keep each log entry on a single line."""
        return v.rstrip("\n\r")


# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #
def _ensure_log_file_writable() -> None:
    """
    Ensure that the log file can be opened for appending.
    Raises HTTPException(500) if the directory or file is not writable.
    """
    try:
        # Create the file if it does not exist.
        LOG_FILE.touch(exist_ok=True)
        # Try opening it in append mode to verify permissions.
        with LOG_FILE.open("a", encoding="utf-8"):
            pass
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="Logging service is unavailable (cannot write to log file).",
        ) from exc


def _rotate_log_if_necessary() -> None:
    """
    Truncate the log file if it exceeds the configured maximum size.
    The simplest strategy is to discard the existing content and start fresh.
    """
    if LOG_FILE.is_file() and LOG_FILE.stat().st_size > MAX_LOG_FILE_SIZE:
        try:
            LOG_FILE.write_text("", encoding="utf-8")
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail="Failed to rotate log file.",
            ) from exc


def append_log(message: str) -> None:
    """
    Append a single log line to the log file after performing safety checks.
    """
    _ensure_log_file_writable()
    _rotate_log_if_necessary()
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(message + "\n")
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Failed to write log entry.") from exc


def read_logs() -> str:
    """
    Return the entire content of the log file as a plain‑text string.
    If the file does not exist, an empty string is returned.
    """
    if not LOG_FILE.is_file():
        return ""
    try:
        return LOG_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Failed to read log file.") from exc


# --------------------------------------------------------------------------- #
# API endpoints
# --------------------------------------------------------------------------- #
@app.post(
    "/log",
    summary="Log a message",
    description="Log a message to the server.",
    responses={200: {"description": "Log message successful"}},
)
def log_message(payload: LogMessage):
    """
    Store a user‑provided message in the server‑side log file.
    The message length is limited by the Pydantic model.
    """
    append_log(payload.message)
    return {"detail": "Log message successful"}


@app.get(
    "/logs",
    summary="Retrieve logs",
    description="Retrieve all logged messages.",
    responses={
        200: {
            "description": "Logs retrieved successfully",
            "content": {"text/plain": {"schema": {"type": "string"}}},
        }
    },
    response_class=Response,
)
def get_logs():
    """
    Return the raw log file content with a `text/plain` media type.
    """
    content = read_logs()
    # Explicit charset to avoid any ambiguity in client rendering.
    return Response(content=content, media_type="text/plain; charset=utf-8")


# --------------------------------------------------------------------------- #
# Application entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)