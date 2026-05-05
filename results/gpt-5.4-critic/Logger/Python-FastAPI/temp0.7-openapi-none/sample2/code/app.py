import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict

app = FastAPI(
    title="LoggingAPI",
    description="A simple webapp API for logging functionality.",
    version="1.0.11",
)

LOG_FILE = Path("logs.txt")
FILE_LOCK = threading.Lock()

# Resource controls to reduce abuse and uncontrolled growth.
MAX_MESSAGE_LENGTH = 4096
MAX_LOG_RESPONSE_BYTES = 1024 * 1024  # 1 MiB
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 60

# Optional access control for log retrieval. If unset, retrieval remains open.
LOGS_ACCESS_TOKEN = os.getenv("LOGS_ACCESS_TOKEN")

_RATE_LIMIT_STORAGE: dict[str, deque[float]] = defaultdict(deque)
_RATE_LIMIT_LOCK = threading.Lock()


class LogRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    message: str | None = None


def _client_identifier(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        first_ip = forwarded_for.split(",")[0].strip()
        if first_ip:
            return first_ip
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _check_rate_limit(request: Request) -> None:
    identifier = _client_identifier(request)
    now = time.monotonic()

    with _RATE_LIMIT_LOCK:
        bucket = _RATE_LIMIT_STORAGE[identifier]
        cutoff = now - RATE_LIMIT_WINDOW_SECONDS

        while bucket and bucket[0] < cutoff:
            bucket.popleft()

        if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
            raise HTTPException(status_code=429, detail="Too Many Requests")

        bucket.append(now)


def _ensure_log_file_exists() -> None:
    try:
        parent = LOG_FILE.parent
        if parent and parent != Path("."):
            parent.mkdir(parents=True, exist_ok=True)
        LOG_FILE.touch(exist_ok=True)
    except OSError as exc:
        raise RuntimeError("Unable to initialize log storage") from exc


@app.on_event("startup")
def startup() -> None:
    try:
        _ensure_log_file_exists()
    except RuntimeError:
        # Keep startup resilient; requests will still return controlled errors
        # if filesystem access is unavailable.
        pass


@app.post("/log", summary="Log a message", description="Log a message to the server.")
def log_message(payload: LogRequest, request: Request) -> Response:
    _check_rate_limit(request)

    if payload.message is None:
        raise HTTPException(status_code=400, detail="message is required")

    if len(payload.message) > MAX_MESSAGE_LENGTH:
        raise HTTPException(status_code=413, detail="message too large")

    sanitized_message = payload.message.replace("\r", " ").replace("\n", " ")

    try:
        with FILE_LOCK:
            _ensure_log_file_exists()
            with LOG_FILE.open("a", encoding="utf-8") as file:
                file.write(f"{sanitized_message}\n")
    except OSError:
        raise HTTPException(status_code=500, detail="Failed to write log message")

    return Response(status_code=200)


@app.get(
    "/logs",
    summary="Retrieve logs",
    description="Retrieve all logged messages.",
    response_class=PlainTextResponse,
)
def get_logs(request: Request) -> PlainTextResponse:
    _check_rate_limit(request)

    if LOGS_ACCESS_TOKEN:
        provided_token = request.headers.get("x-api-key")
        if provided_token != LOGS_ACCESS_TOKEN:
            raise HTTPException(status_code=403, detail="Forbidden")

    try:
        with FILE_LOCK:
            _ensure_log_file_exists()
            file_size = LOG_FILE.stat().st_size
            if file_size > MAX_LOG_RESPONSE_BYTES:
                raise HTTPException(status_code=413, detail="Log file too large to retrieve")

            with LOG_FILE.open("r", encoding="utf-8") as file:
                content = file.read(MAX_LOG_RESPONSE_BYTES + 1)
    except HTTPException:
        raise
    except OSError:
        raise HTTPException(status_code=500, detail="Failed to read logs")

    if len(content.encode("utf-8")) > MAX_LOG_RESPONSE_BYTES:
        raise HTTPException(status_code=413, detail="Log file too large to retrieve")

    return PlainTextResponse(content=content, status_code=200)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)