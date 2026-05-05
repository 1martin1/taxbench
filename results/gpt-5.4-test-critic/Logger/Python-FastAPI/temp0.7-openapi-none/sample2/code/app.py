from pathlib import Path
from threading import Lock

import uvicorn
from fastapi import FastAPI, HTTPException, Response, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field


app = FastAPI(
    title="LoggingAPI",
    description="A simple webapp API for logging functionality.",
    version="1.0.11",
)

LOG_FILE = Path("logs.txt")
FILE_LOCK = Lock()

MAX_MESSAGE_LENGTH = 4096
MAX_LOG_FILE_SIZE = 1024 * 1024  # 1 MiB
LOGS_ACCESS_TOKEN = "read-logs"


class LogRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(..., min_length=1, max_length=MAX_MESSAGE_LENGTH)


def ensure_log_file() -> None:
    if not LOG_FILE.exists():
        with FILE_LOCK:
            if not LOG_FILE.exists():
                LOG_FILE.touch(mode=0o600, exist_ok=True)


def sanitize_message(message: str) -> str:
    return message.replace("\n", " ").replace("\r", " ")


@app.post(
    "/log",
    summary="Log a message",
    description="Log a message to the server.",
    status_code=status.HTTP_200_OK,
    response_class=Response,
    responses={200: {"description": "Log message successful"}},
)
def log_message(payload: LogRequest):
    sanitized_message = sanitize_message(payload.message)
    encoded_line = (sanitized_message + "\n").encode("utf-8")

    ensure_log_file()

    with FILE_LOCK:
        try:
            current_size = LOG_FILE.stat().st_size
        except FileNotFoundError:
            current_size = 0

        if current_size + len(encoded_line) > MAX_LOG_FILE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Log storage limit exceeded",
            )

        with LOG_FILE.open("a", encoding="utf-8", newline="") as file:
            file.write(sanitized_message + "\n")

    return Response(status_code=status.HTTP_200_OK)


@app.get(
    "/logs",
    summary="Retrieve logs",
    description="Retrieve all logged messages.",
    response_class=PlainTextResponse,
    responses={
        200: {
            "description": "Logs retrieved successfully",
            "content": {"text/plain": {"schema": {"type": "string"}}},
        }
    },
)
def get_logs(token: str):
    if token != LOGS_ACCESS_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    ensure_log_file()

    with FILE_LOCK:
        try:
            file_size = LOG_FILE.stat().st_size
        except FileNotFoundError:
            file_size = 0

        if file_size > MAX_LOG_FILE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Log file too large to retrieve",
            )

        with LOG_FILE.open("r", encoding="utf-8", newline="") as file:
            content = file.read()

    return PlainTextResponse(content=content, status_code=status.HTTP_200_OK)


if __name__ == "__main__":
    ensure_log_file()
    uvicorn.run(app, host="0.0.0.0", port=5000)