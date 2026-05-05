import os
import re
import shlex
import subprocess
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator


MAX_FILTER_FLAGS_LENGTH = 64
MAX_COMMAND_REGEX_LENGTH = 128
MAX_PS_OUTPUT_BYTES = 1024 * 1024
PS_TIMEOUT_SECONDS = 5.0
MAX_RESULTS = 10000

SAFE_FILTER_FLAG_PATTERN = re.compile(r"^[A-Za-z0-9\-\s]+$")
SAFE_COMMAND_REGEX_PATTERN = re.compile(r"^[A-Za-z0-9\s._*\-\^\$\[\]\(\)\|\?\+\\/:,=@%]+$")


app = FastAPI(
    title="Monitor Service",
    description="A service that accepts a filter criterion and returns a list of matching process IDs.",
    version="1.0.0",
)


class ErrorResponse(BaseModel):
    code: int = Field(..., description="Numeric error code")
    message: str = Field(..., description="Human-readable error message")


class MonitorRequest(BaseModel):
    filter_flags: Optional[str] = Field(
        default=None,
        description='Flags to filter and sort processes, matching the flags of the "ps" command',
        examples=["aux -T"],
    )
    command_regex: str = Field(
        ...,
        description="The regular expression of the commands to search for on the server",
        examples=["ps.*"],
    )

    @field_validator("filter_flags")
    @classmethod
    def validate_filter_flags(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if len(value) > MAX_FILTER_FLAGS_LENGTH:
            raise ValueError(f"filter_flags must be at most {MAX_FILTER_FLAGS_LENGTH} characters")
        if not value.strip():
            return None
        if not SAFE_FILTER_FLAG_PATTERN.fullmatch(value):
            raise ValueError("filter_flags contains unsupported characters")
        return value

    @field_validator("command_regex")
    @classmethod
    def validate_command_regex(cls, value: str) -> str:
        if len(value) > MAX_COMMAND_REGEX_LENGTH:
            raise ValueError(f"command_regex must be at most {MAX_COMMAND_REGEX_LENGTH} characters")
        if not value:
            raise ValueError("command_regex must not be empty")
        if not SAFE_COMMAND_REGEX_PATTERN.fullmatch(value):
            raise ValueError("command_regex contains unsupported characters")
        return value


class ProcessItem(BaseModel):
    processId: int = Field(..., description="The ID of the process")
    processString: str = Field(..., description="The complete string of the process")


def _error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(code=status_code, message=message).model_dump(),
    )


def _build_ps_command(filter_flags: Optional[str]) -> List[str]:
    command = ["ps"]

    if filter_flags:
        try:
            extra_args = shlex.split(filter_flags)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid filter_flags: {exc}") from exc

        if len(extra_args) > 8:
            raise HTTPException(status_code=400, detail="Too many filter_flags arguments")

        for arg in extra_args:
            if not arg:
                continue
            if arg in {"-o", "--format"} or arg.startswith("--format=") or arg.startswith("-o"):
                raise HTTPException(status_code=400, detail="Overriding output format is not allowed")
            command.append(arg)

    command.extend(["-o", "pid,args"])
    return command


def _run_ps_command(ps_command: List[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ps_command,
            capture_output=True,
            text=False,
            check=False,
            timeout=PS_TIMEOUT_SECONDS,
            env={**os.environ, "LANG": "C", "LC_ALL": "C"},
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=500, detail="ps command timed out") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail="ps command is not available on this server") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to execute ps: {exc}") from exc


def _list_processes(filter_flags: Optional[str]) -> List[ProcessItem]:
    ps_command = _build_ps_command(filter_flags)
    result = _run_ps_command(ps_command)

    stdout_bytes = result.stdout or b""
    stderr_bytes = result.stderr or b""

    if len(stdout_bytes) > MAX_PS_OUTPUT_BYTES or len(stderr_bytes) > MAX_PS_OUTPUT_BYTES:
        raise HTTPException(status_code=400, detail="ps output exceeded allowed size")

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")

    if result.returncode != 0:
        message = stderr.strip() or "ps command failed"
        raise HTTPException(status_code=400, detail=message)

    lines = stdout.splitlines()
    if not lines:
        return []

    processes: List[ProcessItem] = []

    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            continue

        parts = stripped.split(None, 1)
        if not parts:
            continue

        try:
            pid = int(parts[0])
        except ValueError:
            continue

        processes.append(ProcessItem(processId=pid, processString=stripped))
        if len(processes) >= MAX_RESULTS:
            break

    return processes


def _safe_regex_match(pattern: str, text: str) -> bool:
    if pattern == ".*":
        return True
    if not any(ch in pattern for ch in ".^$*+?{}[]|()\\"):
        return pattern in text
    try:
        return re.search(pattern, text) is not None
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid command_regex: {exc}") from exc


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return _error_response(exc.status_code, message)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError):
    errors = exc.errors()
    if errors:
        first = errors[0]
        message = first.get("msg", "Invalid request")
    else:
        message = "Invalid request"
    return _error_response(400, message)


@app.post(
    "/monitor/commands",
    response_model=List[ProcessItem],
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    summary="Retrieve a list of running processes",
    description="Returns the list of processes running on the server whose command string matches a given regular expression.",
)
async def monitor_commands(payload: MonitorRequest) -> List[ProcessItem]:
    try:
        re.compile(payload.command_regex)
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid command_regex: {exc}") from exc

    processes = _list_processes(payload.filter_flags)
    return [proc for proc in processes if _safe_regex_match(payload.command_regex, proc.processString)]


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)