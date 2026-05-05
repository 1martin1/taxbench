from pathlib import Path
from typing import Annotated
import os
import secrets

import anyio
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Response, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator


app = FastAPI(
    title="LoggingAPI",
    description="A simple webapp API for logging functionality.",
    version="1.0.11",
)

BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "logs.txt"
MAX_MESSAGE_LENGTH = 4096
MAX_LOG_READ_BYTES = 1024 * 1024  # 1 MiB
LOGS_AUTH_TOKEN = os.getenv("LOGS_AUTH_TOKEN")


class LogRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: Annotated[str, Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)]

    @field_validator("message")
    @classmethod
    def validate_and_sanitize_message(cls, value: str) -> str:
        sanitized = []
        for char in value:
            if char in ("\r", "\n"):
                sanitized.append(" ")
            elif char.isprintable() or char == "\t":
                sanitized.append(char)
            else:
                sanitized.append(" ")
        normalized = "".join(sanitized).strip()
        if not normalized:
            raise ValueError("message must not be empty")
        return normalized


async def ensure_log_file_exists() -> None:
    def _ensure() -> None:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOG_FILE.touch(exist_ok=True)

    await anyio.to_thread.run_sync(_ensure)


async def append_log_line(message: str) -> None:
    def _append() -> None:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as file:
            file.write(f"{message}\n")

    await anyio.to_thread.run_sync(_append)


async def read_logs_limited() -> str:
    def _read() -> str:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOG_FILE.touch(exist_ok=True)
        file_size = LOG_FILE.stat().st_size
        if file_size > MAX_LOG_READ_BYTES:
            raise ValueError("Log file too large to retrieve safely")
        with LOG_FILE.open("r", encoding="utf-8") as file:
            return file.read(MAX_LOG_READ_BYTES + 1)

    content = await anyio.to_thread.run_sync(_read)
    if len(content.encode("utf-8")) > MAX_LOG_READ_BYTES:
        raise ValueError("Log file too large to retrieve safely")
    return content


def require_logs_auth(x_logs_token: str | None) -> None:
    if not LOGS_AUTH_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Logs retrieval is not configured",
        )
    if x_logs_token is None or not secrets.compare_digest(x_logs_token, LOGS_AUTH_TOKEN):
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
        }
    },
)
async def log_message(payload: LogRequest):
    try:
        await append_log_line(payload.message)
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
        }
    },
)
async def get_logs(x_logs_token: Annotated[str | None, Header(alias="X-Logs-Token")] = None):
    require_logs_auth(x_logs_token)

    try:
        content = await read_logs_limited()
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Log file too large to retrieve",
        )
    except OSError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read logs",
        )

    return PlainTextResponse(content=content, status_code=200)


@app.on_event("startup")
async def startup() -> None:
    try:
        await ensure_log_file_exists()
    except OSError:
        # Defer failure to request time while keeping startup resilient.
        pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)