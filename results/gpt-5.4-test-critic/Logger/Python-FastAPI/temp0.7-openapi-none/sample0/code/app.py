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


class LogRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)


def ensure_log_file() -> None:
    try:
        LOG_FILE.touch(exist_ok=True)
    except OSError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Log storage unavailable",
        )


@app.post(
    "/log",
    summary="Log a message",
    description="Log a message to the server.",
    status_code=200,
    response_class=Response,
)
def log_message(payload: LogRequest):
    sanitized_message = payload.message.replace("\r", " ").replace("\n", " ").strip()
    if not sanitized_message:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Message must not be empty",
        )

    try:
        with FILE_LOCK:
            ensure_log_file()
            with LOG_FILE.open("a", encoding="utf-8") as file:
                file.write(f"{sanitized_message}\n")
    except HTTPException:
        raise
    except OSError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to write log",
        )

    return Response(status_code=200)


@app.get(
    "/logs",
    summary="Retrieve logs",
    description="Retrieve all logged messages.",
    response_class=PlainTextResponse,
)
def get_logs():
    try:
        with FILE_LOCK:
            ensure_log_file()
            content = LOG_FILE.read_text(encoding="utf-8")
    except HTTPException:
        raise
    except OSError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read logs",
        )

    return PlainTextResponse(content=content, status_code=200)


if __name__ == "__main__":
    try:
        ensure_log_file()
    except HTTPException:
        pass
    uvicorn.run(app, host="0.0.0.0", port=5000)