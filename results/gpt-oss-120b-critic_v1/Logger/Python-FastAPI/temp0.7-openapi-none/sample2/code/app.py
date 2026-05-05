from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field, validator
from pathlib import Path
import threading
import time
from collections import defaultdict

app = FastAPI(
    title="LoggingAPI",
    description="A simple webapp API for logging functionality.",
    version="1.0.11",
)

# Configuration constants
MAX_MESSAGE_LENGTH = 2048  # characters
MAX_LOG_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
RATE_LIMIT_PER_MINUTE = 20  # requests per client IP per endpoint

# Resolve log file path safely (same directory as this script)
LOG_FILE = Path(__file__).with_name("logs.txt")

# Ensure the log file exists and is writable at startup
_lock = threading.Lock()
try:
    LOG_FILE.touch(exist_ok=True)
    LOG_FILE.open("a", encoding="utf-8").close()
except Exception:
    # If we cannot create the file, the application should not start
    raise RuntimeError("Unable to access or create the log file.")

# Simple in‑memory rate limiter
_rate_limit_data = defaultdict(lambda: {"count": 0, "reset": time.time() + 60})


def _check_rate_limit(request: Request, limit: int = RATE_LIMIT_PER_MINUTE):
    client_ip = request.client.host if request.client else "unknown"
    data = _rate_limit_data[client_ip]
    now = time.time()
    if now > data["reset"]:
        data["count"] = 0
        data["reset"] = now + 60
    data["count"] += 1
    if data["count"] > limit:
        raise HTTPException(status_code=429, detail="Too Many Requests")


class LogMessage(BaseModel):
    message: str = Field(..., max_length=MAX_MESSAGE_LENGTH)

    @validator("message")
    def strip_newlines(cls, v: str) -> str:
        # Remove trailing newlines to keep each entry on its own line
        return v.rstrip("\n")


@app.post(
    "/log",
    summary="Log a message",
    description="Log a message to the server.",
    responses={200: {"description": "Log message successful"}},
)
def log_message(payload: LogMessage, request: Request):
    """
    Append a validated log message to the plain‑text log file.
    Enforces a size limit on the message and rate‑limits the endpoint.
    """
    _check_rate_limit(request)

    with _lock:
        try:
            with LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(payload.message + "\n")
        except Exception:
            # Do not expose internal details
            raise HTTPException(status_code=500, detail="Internal server error")
    return {"detail": "Log message successful"}


@app.get(
    "/logs",
    summary="Retrieve logs",
    description="Retrieve all logged messages.",
    responses={
        200: {
            "description": "Logs retrieved successfully",
            "content": {"text/plain": {"schema": {"type": "string"}}},
        },
        413: {"description": "Log file too large"},
    },
    response_class=Response,
)
def get_logs(request: Request):
    """
    Return the entire content of the log file as plain text.
    Limits the response size to protect against excessive memory usage.
    """
    _check_rate_limit(request)

    with _lock:
        try:
            if LOG_FILE.stat().st_size > MAX_LOG_FILE_SIZE:
                raise HTTPException(status_code=413, detail="Log file too large")
            content = LOG_FILE.read_text(encoding="utf-8")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=500, detail="Internal server error")
    return Response(content=content, media_type="text/plain")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)