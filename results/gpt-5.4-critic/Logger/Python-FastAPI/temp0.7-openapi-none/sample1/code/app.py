import os
import secrets
import threading
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field


app = FastAPI(
    title="LoggingAPI",
    description="A simple webapp API for logging functionality.",
    version="1.0.11",
)

LOG_FILE = Path("logs.txt")
MAX_MESSAGE_LENGTH = 4096
MAX_LOG_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MiB
READ_CHUNK_SIZE = 64 * 1024
LOG_ACCESS_TOKEN = os.getenv("LOG_ACCESS_TOKEN")
_file_lock = threading.Lock()


class LogRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    message: str = Field(..., min_length=0, max_length=MAX_MESSAGE_LENGTH)


def _require_logs_auth(x_log_token: str | None) -> None:
    if not LOG_ACCESS_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Logs access is not configured",
        )
    if x_log_token is None or not secrets.compare_digest(x_log_token, LOG_ACCESS_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )


@app.post(
    "/log",
    summary="Log a message",
    description="Log a message to the server.",
    status_code=200,
    response_class=Response,
)
def log_message(payload: LogRequest):
    sanitized_message = payload.message.replace("\r", " ").replace("\n", " ")
    encoded_message = (sanitized_message + "\n").encode("utf-8")

    try:
        with _file_lock:
            current_size = LOG_FILE.stat().st_size if LOG_FILE.exists() else 0
            if current_size + len(encoded_message) > MAX_LOG_FILE_SIZE_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
                    detail="Log storage limit reached",
                )

            with LOG_FILE.open("ab") as file:
                file.write(encoded_message)
    except HTTPException:
        raise
    except OSError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to write log message",
        )

    return Response(status_code=200)


@app.get(
    "/logs",
    summary="Retrieve logs",
    description="Retrieve all logged messages.",
    response_class=Response,
    responses={
        200: {
            "description": "Logs retrieved successfully",
            "content": {
                "text/plain": {
                    "schema": {"type": "string"}
                }
            },
        }
    },
)
def get_logs(x_log_token: str | None = Header(default=None, alias="X-Log-Token")):
    _require_logs_auth(x_log_token)

    try:
        with _file_lock:
            if not LOG_FILE.exists():
                return Response(content="", media_type="text/plain")

            parts: list[str] = []
            with LOG_FILE.open("r", encoding="utf-8", errors="replace") as file:
                while True:
                    chunk = file.read(READ_CHUNK_SIZE)
                    if not chunk:
                        break
                    parts.append(chunk)
    except OSError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read logs",
        )

    return Response(content="".join(parts), media_type="text/plain")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)