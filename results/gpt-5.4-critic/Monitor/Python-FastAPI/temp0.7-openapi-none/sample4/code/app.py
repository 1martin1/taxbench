import os
import re
import shlex
import subprocess
from typing import Any, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field


MAX_FILTER_FLAGS_LENGTH = 128
MAX_COMMAND_REGEX_LENGTH = 128
PS_TIMEOUT_SECONDS = 5.0

# Restrict user-controlled ps flags to a small, safe subset of simple short-option
# tokens to avoid expensive listings/sorting/thread enumeration and preserve the API
# shape without allowing arbitrary ps argument injection.
ALLOWED_FILTER_TOKENS = {
    "a",
    "x",
    "u",
    "e",
    "-a",
    "-x",
    "-u",
    "-e",
    "ax",
    "aux",
    "au",
    "ux",
    "ae",
    "axu",
    "auxe",
}

# Allow only a constrained regex subset to reduce ReDoS risk while preserving
# regex-like matching behavior for common patterns.
SAFE_REGEX_PATTERN = re.compile(r"^[A-Za-z0-9\s._/\-^$*+?()[\]|\\]+$")


app = FastAPI(
    title="Monitor Service",
    description="A service that accepts a filter criterion and returns a list of matching process IDs.",
    version="1.0.0",
)


class ErrorModel(BaseModel):
    code: int = Field(..., description="Numeric error code")
    message: str = Field(..., description="Human-readable error message")


class MonitorRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

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


class ProcessInfo(BaseModel):
    processId: int = Field(..., description="The ID of the process")
    processString: str = Field(..., description="The complete string of the process")


def _error_response(status_code: int, message: str) -> JSONResponse:
    error = ErrorModel(code=status_code, message=message)
    return JSONResponse(status_code=status_code, content=error.model_dump())


def _validate_filter_flags(filter_flags: Optional[str]) -> List[str]:
    if filter_flags is None:
        return []

    if not isinstance(filter_flags, str):
        raise HTTPException(status_code=400, detail="filter_flags must be a string.")

    if len(filter_flags) > MAX_FILTER_FLAGS_LENGTH:
        raise HTTPException(status_code=400, detail="filter_flags is too long.")

    if not filter_flags.strip():
        return []

    try:
        tokens = shlex.split(filter_flags)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid filter_flags: {exc}") from exc

    if len(tokens) > 4:
        raise HTTPException(status_code=400, detail="Too many filter_flags tokens.")

    for token in tokens:
        if token in {"command", "command="} or token.startswith("command="):
            raise HTTPException(
                status_code=400,
                detail='The "command" parameter is not supported on this OS.',
            )

        if token not in ALLOWED_FILTER_TOKENS:
            raise HTTPException(status_code=400, detail=f"Unsupported filter_flags token: {token}")

    return tokens


def _validate_command_regex(command_regex: str) -> re.Pattern[str]:
    if not isinstance(command_regex, str):
        raise HTTPException(status_code=400, detail="command_regex must be a string.")

    if not command_regex:
        raise HTTPException(status_code=400, detail="command_regex is required.")

    if len(command_regex) > MAX_COMMAND_REGEX_LENGTH:
        raise HTTPException(status_code=400, detail="command_regex is too long.")

    if not SAFE_REGEX_PATTERN.fullmatch(command_regex):
        raise HTTPException(status_code=400, detail="command_regex contains unsupported characters.")

    # Reject common constructs associated with high backtracking complexity.
    if "(?" in command_regex:
        raise HTTPException(status_code=400, detail="command_regex contains unsupported regex constructs.")
    if re.search(r"(\*|\+|\?|\{[^}]*\})\s*(\*|\+|\?|\{)", command_regex):
        raise HTTPException(status_code=400, detail="command_regex contains unsupported nested quantifiers.")

    try:
        return re.compile(command_regex)
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid command_regex: {exc}") from exc


def _build_ps_command(filter_flags: Optional[str]) -> List[str]:
    cmd = ["ps"]
    cmd.extend(_validate_filter_flags(filter_flags))
    cmd.extend(["-o", "pid=", "-o", "args="])
    return cmd


def _run_ps(filter_flags: Optional[str]) -> str:
    cmd = _build_ps_command(filter_flags)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=PS_TIMEOUT_SECONDS,
            env={"LC_ALL": "C", "LANG": "C"},
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=500, detail="The ps command timed out.") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail="The ps command is not available on this server.") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to execute ps: {exc}") from exc

    if result.returncode != 0:
        stderr = (result.stderr or "").strip() or "ps command failed"
        raise HTTPException(status_code=400, detail=stderr)

    return result.stdout


def _parse_ps_output(output: str) -> List[ProcessInfo]:
    processes: List[ProcessInfo] = []

    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue

        stripped = line.lstrip()
        match = re.match(r"^(\d+)\s+(.*)$", stripped)
        if not match:
            continue

        pid = int(match.group(1))
        process_string = match.group(2)
        processes.append(ProcessInfo(processId=pid, processString=process_string))

    return processes


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return _error_response(exc.status_code, detail)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    message = "Invalid request."
    if errors:
        first = errors[0]
        msg = first.get("msg")
        if isinstance(msg, str) and msg:
            message = msg
    return _error_response(400, message)


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    return _error_response(500, "Internal server error")


@app.post(
    "/monitor/commands",
    response_model=List[ProcessInfo],
    responses={
        400: {"model": ErrorModel},
        500: {"model": ErrorModel},
    },
    summary="Retrieve a list of running processes",
    description="Returns the list of processes running on the server whose command string matches a given regular expression.",
)
async def monitor_commands(request: MonitorRequest) -> List[ProcessInfo]:
    regex = _validate_command_regex(request.command_regex)
    output = _run_ps(request.filter_flags)
    processes = _parse_ps_output(output)
    return [proc for proc in processes if regex.search(proc.processString)]


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)