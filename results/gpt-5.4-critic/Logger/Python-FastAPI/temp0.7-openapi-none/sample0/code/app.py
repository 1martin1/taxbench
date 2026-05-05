import os
import time
from pathlib import Path
from threading import Lock
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
import uvicorn

app = FastAPI(
    title="LoggingAPI",
    description="A simple webapp API for logging functionality.",
    version="1.0.11",
)

LOG_FILE = Path("logs.txt")
FILE_LOCK = Lock()

MAX_MESSAGE_LENGTH = 4096
MAX_LOG_READ_BYTES = 1024 * 1024  # 1 MiB
RATE_LIMIT_WINDOW_SECONDS = 60
POST_RATE_LIMIT = 60
GET_RATE_LIMIT = 30

LOGS_ACCESS_TOKEN = os.getenv("LOGS_ACCESS_TOKEN")

_rate_limit_lock = Lock()
_rate_limit_store: dict[str, dict[str, list[float]]] = {}


class LogRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    message: str = Field(..., min_length=0, max_length=MAX_MESSAGE_LENGTH)


def _client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        first_ip = forwarded_for.split(",")[0].strip()
        if first_ip:
            return first_ip
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _check_rate_limit(client_id: str, route_key: str, limit: int) -> None:
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW_SECONDS

    with _rate_limit_lock:
        client_entry = _rate_limit_store.setdefault(client_id, {})
        timestamps = client_entry.setdefault(route_key, [])
        timestamps[:] = [ts for ts in timestamps if ts >= window_start]

        if len(timestamps) >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
            )

        timestamps.append(now)


def _ensure_log_parent_exists() -> None:
    parent = LOG_FILE.parent
    if parent and str(parent) != ".":
        parent.mkdir(parents=True, exist_ok=True)


def _sanitize_message(message: str) -> str:
    return message.replace("\n", " ").replace("\r", " ")


def _authorize_logs_access(authorization: str | None) -> None:
    if not LOGS_ACCESS_TOKEN:
        return

    expected = f"Bearer {LOGS_ACCESS_TOKEN}"
    if authorization != expected:
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
    responses={
        200: {
            "description": "Log message successful",
            "content": {},
        },
        429: {"description": "Too Many Requests"},
        500: {"description": "Internal Server Error"},
    },
)
def log_message(payload: LogRequest, request: Request) -> Response:
    client_id = _client_ip(request)
    _check_rate_limit(client_id, "POST:/log", POST_RATE_LIMIT)

    sanitized_message = _sanitize_message(payload.message)

    try:
        with FILE_LOCK:
            _ensure_log_parent_exists()
            with LOG_FILE.open("a", encoding="utf-8") as file:
                file.write(sanitized_message + "\n")
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
    response_class=PlainTextResponse,
    responses={
        200: {
            "description": "Logs retrieved successfully",
            "content": {
                "text/plain": {
                    "schema": {"type": "string"}
                }
            },
        },
        401: {"description": "Unauthorized"},
        413: {"description": "Log file too large"},
        429: {"description": "Too Many Requests"},
        500: {"description": "Internal Server Error"},
    },
)
def get_logs(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> PlainTextResponse:
    client_id = _client_ip(request)
    _check_rate_limit(client_id, "GET:/logs", GET_RATE_LIMIT)
    _authorize_logs_access(authorization)

    try:
        with FILE_LOCK:
            if not LOG_FILE.exists():
                return PlainTextResponse(content="", status_code=200)

            file_size = LOG_FILE.stat().st_size
            if file_size > MAX_LOG_READ_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Log file too large to retrieve",
                )

            with LOG_FILE.open("r", encoding="utf-8") as file:
                content = file.read(MAX_LOG_READ_BYTES + 1)

        if len(content.encode("utf-8")) > MAX_LOG_READ_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Log file too large to retrieve",
            )
    except HTTPException:
        raise
    except OSError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read logs",
        )

    return PlainTextResponse(content=content, status_code=200)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)