from pathlib import Path
import threading

import uvicorn
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field


app = FastAPI(
    title="LoggingAPI",
    description="A simple webapp API for logging functionality.",
    version="1.0.11",
)

LOG_FILE = Path("logs.txt")
FILE_LOCK = threading.Lock()
MAX_MESSAGE_LENGTH = 4096
MAX_LOG_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MiB


class LogRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    message: str = Field(..., max_length=MAX_MESSAGE_LENGTH)


def _ensure_log_file() -> None:
    LOG_FILE.touch(exist_ok=True)


@app.post(
    "/log",
    summary="Log a message",
    description="Log a message to the server.",
    status_code=200,
    response_class=Response,
)
def log_message(payload: LogRequest):
    sanitized_message = payload.message.replace("\r", " ").replace("\n", " ")
    encoded_line = f"{sanitized_message}\n".encode("utf-8")

    with FILE_LOCK:
        _ensure_log_file()
        try:
            current_size = LOG_FILE.stat().st_size
        except OSError:
            raise HTTPException(status_code=500, detail="Unable to access log file")

        if current_size + len(encoded_line) > MAX_LOG_FILE_SIZE_BYTES:
            raise HTTPException(status_code=507, detail="Log storage limit reached")

        try:
            with LOG_FILE.open("ab") as file:
                file.write(encoded_line)
        except OSError:
            raise HTTPException(status_code=500, detail="Unable to write log file")

    return Response(status_code=200)


@app.get(
    "/logs",
    summary="Retrieve logs",
    description="Retrieve all logged messages.",
    response_class=PlainTextResponse,
)
def get_logs():
    with FILE_LOCK:
        _ensure_log_file()
        try:
            content = LOG_FILE.read_text(encoding="utf-8")
        except OSError:
            raise HTTPException(status_code=500, detail="Unable to read log file")
    return PlainTextResponse(content=content, status_code=200)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)