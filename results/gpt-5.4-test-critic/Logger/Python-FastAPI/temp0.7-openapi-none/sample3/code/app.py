from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field


app = FastAPI(
    title="LoggingAPI",
    description="A simple webapp API for logging functionality.",
    version="1.0.11",
)

LOG_FILE = Path("logs.txt")
MAX_MESSAGE_LENGTH = 4096


class LogRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=MAX_MESSAGE_LENGTH)


def ensure_log_file_exists() -> None:
    try:
        LOG_FILE.touch(exist_ok=True)
    except OSError:
        raise HTTPException(status_code=500, detail="Unable to access log storage")


@app.post(
    "/log",
    summary="Log a message",
    description="Log a message to the server.",
    status_code=200,
    response_class=Response,
)
def log_message(payload: LogRequest):
    sanitized_message = payload.message.replace("\n", " ").replace("\r", " ")

    try:
        with LOG_FILE.open("a", encoding="utf-8") as file:
            file.write(sanitized_message + "\n")
    except OSError:
        raise HTTPException(status_code=500, detail="Unable to write log message")

    return Response(status_code=200)


@app.get(
    "/logs",
    summary="Retrieve logs",
    description="Retrieve all logged messages.",
    response_class=PlainTextResponse,
)
def get_logs():
    try:
        ensure_log_file_exists()
        content = LOG_FILE.read_text(encoding="utf-8")
    except OSError:
        raise HTTPException(status_code=500, detail="Unable to read logs")

    return PlainTextResponse(content=content, status_code=200)


if __name__ == "__main__":
    try:
        LOG_FILE.touch(exist_ok=True)
    except OSError:
        pass
    uvicorn.run(app, host="0.0.0.0", port=5000)