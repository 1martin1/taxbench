import logging
import time
from pathlib import Path
from typing import Dict, List

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, ValidationError

# ----------------------------------------------------------------------
# Configuration constants
# ----------------------------------------------------------------------
MAX_LOG_FILE_SIZE = 5 * 1024 * 1024          # 5 MB max log file size
MAX_LOG_RESPONSE_SIZE = 1 * 1024 * 1024     # 1 MB max response size
MAX_MESSAGE_SIZE = 10 * 1024                # 10 KB max incoming message size
RATE_LIMIT_WINDOW = 60                      # seconds
RATE_LIMIT_MAX_REQUESTS = 30                # max requests per IP per window

# ----------------------------------------------------------------------
# Setup
# ----------------------------------------------------------------------
app = FastAPI(
    title="LoggingAPI",
    description="A simple webapp API for logging functionality.",
    version="1.0.11",
)

LOG_FILE = Path("logs.txt")
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

# Configure internal logger (does not expose details to clients)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler()],
)

logger = logging.getLogger("logging_api")

# In‑memory rate‑limit store: IP -> list of request timestamps (float)
_rate_limit_store: Dict[str, List[float]] = {}


def _prune_old_requests(timestamps: List[float], now: float) -> List[float]:
    """Remove timestamps older than the rolling window."""
    cutoff = now - RATE_LIMIT_WINDOW
    return [ts for ts in timestamps if ts > cutoff]


def _is_rate_limited(ip: str) -> bool:
    """Return True if the given IP exceeded the request limit."""
    now = time.time()
    timestamps = _rate_limit_store.get(ip, [])
    timestamps = _prune_old_requests(timestamps, now)

    if len(timestamps) >= RATE_LIMIT_MAX_REQUESTS:
        # Too many requests in the current window
        _rate_limit_store[ip] = timestamps  # keep pruned list
        return True

    timestamps.append(now)
    _rate_limit_store[ip] = timestamps
    return False


# ----------------------------------------------------------------------
# Pydantic models
# ----------------------------------------------------------------------
class LogMessage(BaseModel):
    message: str = Field(..., max_length=MAX_MESSAGE_SIZE)


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------
@app.post(
    "/log",
    summary="Log a message",
    description="Log a message to the server.",
    status_code=status.HTTP_200_OK,
    response_description="Log message successful",
)
async def log_message(request: Request, payload: LogMessage):
    client_ip = request.client.host if request.client else "unknown"

    # Rate limiting
    if _is_rate_limited(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests, please try again later.",
        )

    # Enforce message size (already validated by Pydantic, but double‑check raw length)
    if len(payload.message.encode("utf-8")) > MAX_MESSAGE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Message size exceeds allowed limit.",
        )

    try:
        # Prevent the log file from growing beyond the configured limit
        if LOG_FILE.is_file() and LOG_FILE.stat().st_size >= MAX_LOG_FILE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
                detail="Log storage limit reached.",
            )

        # Ensure a single line per log entry
        line = payload.message.rstrip("\n") + "\n"
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line)
    except HTTPException:
        # Propagate intentional HTTP errors unchanged
        raise
    except Exception as exc:
        logger.exception("Failed to write log entry.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal server error occurred.",
        ) from exc

    return {"detail": "Log message successful"}


@app.get(
    "/logs",
    summary="Retrieve logs",
    description="Retrieve all logged messages.",
    response_class=PlainTextResponse,
    responses={
        200: {
            "description": "Logs retrieved successfully",
            "content": {"text/plain": {}},
        }
    },
)
async def get_logs(request: Request):
    client_ip = request.client.host if request.client else "unknown"

    # Rate limiting
    if _is_rate_limited(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests, please try again later.",
        )

    if not LOG_FILE.is_file():
        return ""

    try:
        file_size = LOG_FILE.stat().st_size
        # If the file exceeds the response size limit, truncate safely
        if file_size > MAX_LOG_RESPONSE_SIZE:
            with LOG_FILE.open("rb") as f:
                # Read up to the limit and decode (ignore partial UTF‑8 chars at end)
                raw = f.read(MAX_LOG_RESPONSE_SIZE)
                try:
                    content = raw.decode("utf-8")
                except UnicodeDecodeError:
                    # Decode as much as possible, ignoring incomplete trailing bytes
                    content = raw.decode("utf-8", errors="ignore")
                content += "\n--- LOG TRUNCATED DUE TO SIZE LIMIT ---"
        else:
            content = LOG_FILE.read_text(encoding="utf-8")
        return content
    except Exception as exc:
        logger.exception("Failed to read logs.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal server error occurred.",
        ) from exc


# ----------------------------------------------------------------------
# Application entry point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)