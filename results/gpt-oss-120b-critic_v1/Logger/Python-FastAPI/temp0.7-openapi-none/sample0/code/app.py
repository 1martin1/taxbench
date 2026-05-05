import pathlib
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

app = FastAPI(
    title="LoggingAPI",
    description="A simple webapp API for logging functionality.",
    version="1.0.11",
)

# ----------------------------------------------------------------------
# Configuration & constants
# ----------------------------------------------------------------------
BASE_DIR = pathlib.Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "logs.txt"

# Maximum allowed size for a single log message (in bytes)
MAX_MESSAGE_SIZE = 1024  # 1 KiB

# Maximum amount of log data returned in a single response (in bytes)
MAX_LOG_RESPONSE_SIZE = 1_048_576  # 1 MiB


# ----------------------------------------------------------------------
# Pydantic models
# ----------------------------------------------------------------------
class LogMessage(BaseModel):
    # Enforce a reasonable length at the validation layer as well
    message: str = Field(..., max_length=MAX_MESSAGE_SIZE)


# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------
def _ensure_log_file() -> None:
    """
    Guarantee that the log file exists and is located in a deterministic,
    absolute path. The parent directory is created if necessary.
    """
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.touch(exist_ok=True)


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------
@app.post(
    "/log",
    summary="Log a message",
    description="Log a message to the server.",
)
async def log_message(payload: LogMessage):
    """
    Append a single log message to the log file.
    """
    _ensure_log_file()

    # Additional safety: ensure the raw byte size does not exceed the limit.
    if len(payload.message.encode("utf-8")) > MAX_MESSAGE_SIZE:
        raise HTTPException(
            status_code=413,
            detail="Log message exceeds maximum allowed size.",
        )

    try:
        # Write the message atomically to avoid interleaved writes.
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(payload.message.rstrip("\n") + "\n")
    except OSError:
        # Do not expose internal error details.
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred while writing the log.",
        ) from None

    return {"detail": "Log stored successfully"}


@app.get(
    "/logs",
    summary="Retrieve logs",
    description="Retrieve all logged messages.",
    responses={200: {"content": {"text/plain": {}}}},
)
async def get_logs():
    """
    Return the content of the log file as plain text.
    The response is limited to a configurable maximum size to protect
    against memory exhaustion.
    """
    _ensure_log_file()

    try:
        with LOG_FILE.open("r", encoding="utf-8") as f:
            # Read up to the maximum allowed size plus one extra byte to detect truncation.
            raw_content = f.read(MAX_LOG_RESPONSE_SIZE + 1)
    except OSError:
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred while reading the logs.",
        ) from None

    # If the file is larger than the allowed response size, truncate and indicate it.
    if len(raw_content) > MAX_LOG_RESPONSE_SIZE:
        content = raw_content[:MAX_LOG_RESPONSE_SIZE] + "\n--- LOG TRUNCATED ---\n"
    else:
        content = raw_content

    return Response(content=content, media_type="text/plain")


# ----------------------------------------------------------------------
# Application entry point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)