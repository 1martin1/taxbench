import os
import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel, Field, validator
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse as StarletteJSONResponse
import uvicorn

# Configuration constants
LOG_FILE = "logs.txt"
MAX_MESSAGE_LENGTH = 1024               # Maximum allowed length for a log message
MAX_LOG_FILE_SIZE = 5 * 1024 * 1024     # 5 MB maximum size for the log file
MAX_REQUEST_SIZE = 1 * 1024 * 1024      # 1 MB maximum request body size

# Async-compatible lock to protect file access
_file_lock = anyio.create_lock()


class LogMessage(BaseModel):
    message: str = Field(..., max_length=MAX_MESSAGE_LENGTH, description="Log message to be stored")

    @validator("message")
    def strip_newlines(cls, v: str) -> str:
        """Ensure the message does not contain trailing newlines."""
        return v.rstrip("\n")


def _ensure_log_file():
    """Guarantee that the log file exists."""
    if not os.path.isfile(LOG_FILE):
        # Use a context manager for safety
        with open(LOG_FILE, "a", encoding="utf-8"):
            pass


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """Reject requests with a body larger than the configured limit."""

    def __init__(self, app, max_body_size: int):
        super().__init__(app)
        self.max_body_size = max_body_size

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_body_size:
                    return StarletteJSONResponse(
                        {"detail": "Request body too large"},
                        status_code=413,
                    )
            except ValueError:
                # Invalid header; proceed to normal handling which may raise later
                pass
        return await call_next(request)


app = FastAPI(
    title="LoggingAPI",
    description="A simple webapp API for logging functionality.",
    version="1.0.11",
)

# Apply the request size limiting middleware
app.add_middleware(MaxBodySizeMiddleware, max_body_size=MAX_REQUEST_SIZE)


async def _write_log(message: str):
    """Write a log entry to the file in a thread to avoid blocking the event loop."""
    def sync_write():
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(message + "\n")
    await anyio.to_thread.run_sync(sync_write)


async def _read_logs() -> str:
    """Read the entire log file content in a thread to avoid blocking."""
    def sync_read():
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return f.read()
    return await anyio.to_thread.run_sync(sync_read)


@app.post(
    "/log",
    summary="Log a message",
    description="Log a message to the server.",
)
async def create_log(entry: LogMessage):
    """
    Append a single log entry to the log file.
    """
    _ensure_log_file()
    async with _file_lock:
        try:
            await _write_log(entry.message)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to write log: {exc}")
    return {"detail": "Log message successful"}


@app.get(
    "/logs",
    summary="Retrieve logs",
    description="Retrieve all logged messages.",
    responses={200: {"content": {"text/plain": {}}}},
)
async def get_logs():
    """
    Return the content of the log file as plain text.
    """
    _ensure_log_file()
    # Prevent serving an excessively large log file
    try:
        file_size = os.path.getsize(LOG_FILE)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to access logs: {exc}")

    if file_size > MAX_LOG_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail="Log file too large to retrieve",
        )

    async with _file_lock:
        try:
            content = await _read_logs()
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to read logs: {exc}")

    return Response(content=content, media_type="text/plain")


if __name__ == "__main__":
    # Run the FastAPI application with uvicorn on 0.0.0.0:5000
    uvicorn.run(app, host="0.0.0.0", port=5000)