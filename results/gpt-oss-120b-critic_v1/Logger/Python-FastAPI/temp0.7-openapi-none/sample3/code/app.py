import os
import threading
import time
from typing import Dict, Tuple

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, validator

# --------------------------------------------------------------------------- #
# Configuration constants
# --------------------------------------------------------------------------- #
LOG_FILE = "logs.txt"
MAX_MESSAGE_LENGTH = 1_000               # Max characters per log entry
MAX_LOG_FILE_SIZE = 5 * 1024 * 1024       # 5 MB total log file size
MAX_RESPONSE_SIZE = 1 * 1024 * 1024       # 1 MB max payload returned to client
MAX_REQUEST_BODY_SIZE = 2 * 1024          # 2 KB max request body size
RATE_LIMIT_WINDOW = 60                    # seconds
RATE_LIMIT_MAX_REQUESTS = 30              # per IP per window

# --------------------------------------------------------------------------- #
# Global state (thread‑safe)
# --------------------------------------------------------------------------- #
_log_lock = threading.Lock()
_rate_limit_store: Dict[str, Tuple[float, int]] = {}   # ip -> (window_start, count)

# --------------------------------------------------------------------------- #
# FastAPI application
# --------------------------------------------------------------------------- #
app = FastAPI(
    title="LoggingAPI",
    description="A simple webapp API for logging functionality.",
    version="1.0.11",
)

# --------------------------------------------------------------------------- #
# Pydantic model with validation
# --------------------------------------------------------------------------- #
class LogMessage(BaseModel):
    message: str

    @validator("message")
    def message_length(cls, v: str) -> str:
        if len(v) > MAX_MESSAGE_LENGTH:
            raise ValueError(f"Message exceeds maximum length of {MAX_MESSAGE_LENGTH} characters")
        # Disallow newline characters to keep each entry on a single line
        if "\n" in v or "\r" in v:
            raise ValueError("Message must not contain newline characters")
        return v

# --------------------------------------------------------------------------- #
# Middleware: rate limiting & request size enforcement
# --------------------------------------------------------------------------- #
@app.middleware("http")
async def security_middleware(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"

    # ---- Rate limiting ----
    now = time.time()
    window_start, count = _rate_limit_store.get(client_ip, (now, 0))
    if now - window_start < RATE_LIMIT_WINDOW:
        if count >= RATE_LIMIT_MAX_REQUESTS:
            return PlainTextResponse("Too Many Requests", status_code=429)
        _rate_limit_store[client_ip] = (window_start, count + 1)
    else:
        _rate_limit_store[client_ip] = (now, 1)

    # ---- Request body size enforcement (via Content‑Length header) ----
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_REQUEST_BODY_SIZE:
                return PlainTextResponse("Payload Too Large", status_code=413)
        except ValueError:
            pass  # ignore malformed header; FastAPI will handle later

    response = await call_next(request)
    return response

# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #
def _ensure_log_file_within_limit() -> None:
    """
    Raise HTTPException if the log file would exceed the configured maximum size.
    """
    if os.path.exists(LOG_FILE):
        current_size = os.path.getsize(LOG_FILE)
        if current_size >= MAX_LOG_FILE_SIZE:
            raise HTTPException(
                status_code=507,
                detail="Log storage limit reached"
            )

def _read_logs_safely() -> str:
    """
    Read logs up to MAX_RESPONSE_SIZE bytes.
    If the file is larger, raise an error to avoid memory exhaustion.
    """
    if not os.path.exists(LOG_FILE):
        return ""

    file_size = os.path.getsize(LOG_FILE)
    if file_size > MAX_RESPONSE_SIZE:
        raise HTTPException(
            status_code=413,
            detail="Log file too large to retrieve"
        )

    with open(LOG_FILE, "r", encoding="utf-8") as f:
        return f.read()

# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.post(
    "/log",
    summary="Log a message",
    description="Log a message to the server.",
    response_description="Log message successful",
)
def log_message(entry: LogMessage):
    """
    Append a validated log line to the log file.
    """
    with _log_lock:
        _ensure_log_file_within_limit()
        line = entry.message.rstrip("\n") + "\n"
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            raise HTTPException(
                status_code=500,
                detail="Failed to write log entry"
            )
    return {"detail": "Log message successful"}

@app.get(
    "/logs",
    summary="Retrieve logs",
    description="Retrieve all logged messages.",
    response_description="Logs retrieved successfully",
    response_class=PlainTextResponse,
)
def get_logs():
    """
    Return the log file content as plain text, respecting size limits.
    """
    with _log_lock:
        try:
            content = _read_logs_safely()
        except OSError:
            raise HTTPException(
                status_code=500,
                detail="Failed to read log file"
            )
    return PlainTextResponse(content, media_type="text/plain")

# --------------------------------------------------------------------------- #
# Run the server
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)