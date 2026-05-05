import os
import html
from pathlib import Path
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field, ValidationError

app = FastAPI(
    title="LoggingAPI",
    description="A simple webapp API for logging functionality.",
    version="1.0.11",
)

# Define a dedicated directory for logs to avoid accidental path traversal.
BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "log_data"
LOG_FILE = LOG_DIR / "logs.txt"


class LogMessage(BaseModel):
    message: str = Field(..., min_length=1, max_length=10_000)


def ensure_log_file() -> None:
    """
    Ensure that the log directory and file exist and are regular files.
    This function also guards against symlink attacks.
    """
    try:
        # Create the directory if it does not exist.
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to create log directory: {exc}"
        )

    # Resolve the absolute path and ensure it is within LOG_DIR.
    try:
        resolved_path = LOG_FILE.resolve(strict=False)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to resolve log file path: {exc}"
        )

    if LOG_DIR not in resolved_path.parents:
        raise HTTPException(
            status_code=500,
            detail="Log file path is outside the allowed directory.",
        )

    # If the file exists, ensure it is not a symlink.
    if resolved_path.exists():
        if resolved_path.is_symlink():
            raise HTTPException(
                status_code=500,
                detail="Log file is a symlink, which is not allowed.",
            )
        if not resolved_path.is_file():
            raise HTTPException(
                status_code=500,
                detail="Log path exists but is not a regular file.",
            )
    else:
        # Create an empty regular file.
        try:
            resolved_path.touch(exist_ok=False)
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"Failed to create log file: {exc}"
            )


@app.post(
    "/log",
    summary="Log a message",
    description="Log a message to the server.",
)
async def log_message(payload: LogMessage):
    """
    Append the received message to the log file, one line per message.
    """
    ensure_log_file()
    try:
        # Write the message safely, stripping trailing newlines to keep one per entry.
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(payload.message.rstrip("\n") + "\n")
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
    Return the entire content of the log file as plain text.
    The content is HTML‑escaped to prevent XSS when rendered in browsers.
    """
    ensure_log_file()
    try:
        with LOG_FILE.open("r", encoding="utf-8") as f:
            raw_content = f.read()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read logs: {exc}")

    # Escape HTML characters to mitigate reflected XSS.
    escaped_content = html.escape(raw_content, quote=False)
    return Response(content=escaped_content, media_type="text/plain")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)