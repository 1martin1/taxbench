from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field


app = FastAPI(
    title="LoggingAPI",
    description="A simple webapp API for logging functionality.",
    version="1.0.11",
)

LOG_FILE = Path("logs.txt")
FILE_LOCK = Lock()

MAX_MESSAGE_LENGTH = 4096
MAX_LOG_RESPONSE_BYTES = 1024 * 1024  # 1 MiB
RATE_LIMIT_WINDOW_SECONDS = 60.0
RATE_LIMIT_MAX_REQUESTS = 60

_rate_limit_lock = Lock()
_rate_limit_store: dict[str, list[float]] = {}


class LogRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    message: str = Field(..., min_length=1, max_length=MAX_MESSAGE_LENGTH)


def _get_client_identifier(request: Request) -> str:
    client = request.client
    if client is None or not client.host:
        return "unknown"
    return client.host


def _enforce_rate_limit(request: Request) -> None:
    now = monotonic()
    client_id = _get_client_identifier(request)

    with _rate_limit_lock:
        timestamps = _rate_limit_store.get(client_id, [])
        cutoff = now - RATE_LIMIT_WINDOW_SECONDS
        timestamps = [ts for ts in timestamps if ts >= cutoff]

        if len(timestamps) >= RATE_LIMIT_MAX_REQUESTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
            )

        timestamps.append(now)
        _rate_limit_store[client_id] = timestamps


def _ensure_log_file_exists() -> None:
    try:
        with FILE_LOCK:
            LOG_FILE.touch(exist_ok=True)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Log storage is unavailable",
        ) from exc


@app.on_event("startup")
def startup() -> None:
    try:
        with FILE_LOCK:
            LOG_FILE.touch(exist_ok=True)
    except OSError:
        # Defer filesystem errors to request handling so the server can still start.
        pass


@app.post(
    "/log",
    summary="Log a message",
    description="Log a message to the server.",
    status_code=status.HTTP_200_OK,
    response_class=Response,
    responses={
        200: {"description": "Log message successful"},
        429: {"description": "Too Many Requests"},
        500: {"description": "Log storage is unavailable"},
    },
)
def log_message(payload: LogRequest, request: Request) -> Response:
    _enforce_rate_limit(request)
    _ensure_log_file_exists()

    sanitized_message = payload.message.replace("\n", " ").replace("\r", " ")

    try:
        with FILE_LOCK:
            with LOG_FILE.open("a", encoding="utf-8") as file:
                file.write(sanitized_message + "\n")
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to write log message",
        ) from exc

    return Response(status_code=status.HTTP_200_OK)


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
                    "schema": {"type": "string"},
                }
            },
        },
        413: {"description": "Log file too large to return"},
        429: {"description": "Too Many Requests"},
        500: {"description": "Log storage is unavailable"},
    },
)
def get_logs(request: Request) -> Response:
    _enforce_rate_limit(request)
    _ensure_log_file_exists()

    try:
        with FILE_LOCK:
            file_size = LOG_FILE.stat().st_size
            if file_size > MAX_LOG_RESPONSE_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Log file too large to return",
                )

            chunks: list[str] = []
            total_bytes = 0
            with LOG_FILE.open("r", encoding="utf-8") as file:
                for chunk in iter(lambda: file.read(8192), ""):
                    chunk_bytes = len(chunk.encode("utf-8"))
                    total_bytes += chunk_bytes
                    if total_bytes > MAX_LOG_RESPONSE_BYTES:
                        raise HTTPException(
                            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail="Log file too large to return",
                        )
                    chunks.append(chunk)
    except HTTPException:
        raise
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read logs",
        ) from exc

    return Response(content="".join(chunks), media_type="text/plain")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)