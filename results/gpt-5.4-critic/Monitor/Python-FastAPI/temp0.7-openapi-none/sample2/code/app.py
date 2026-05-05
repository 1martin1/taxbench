import re
import shlex
import subprocess
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator


MAX_FILTER_FLAGS_LENGTH = 128
MAX_COMMAND_REGEX_LENGTH = 256
MAX_PS_ARGS = 16
MAX_PS_OUTPUT_BYTES = 2 * 1024 * 1024
PS_TIMEOUT_SECONDS = 3.0

SAFE_REGEX_META_CHARS = set(r".^$*+?{}[]\|()")


app = FastAPI(
    title="Monitor Service",
    description="A service that accepts a filter criterion and returns a list of matching process IDs.",
    version="1.0.0",
)


class ErrorResponse(BaseModel):
    code: int = Field(..., description="Numeric error code")
    message: str = Field(..., description="Human-readable error message")


class MonitorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    def validate_filter_flags_length(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and len(value) > MAX_FILTER_FLAGS_LENGTH:
            raise ValueError(f"filter_flags must be at most {MAX_FILTER_FLAGS_LENGTH} characters")
        return value

    @field_validator("command_regex")
    @classmethod
    def validate_command_regex_length(cls, value: str) -> str:
        if len(value) > MAX_COMMAND_REGEX_LENGTH:
            raise ValueError(f"command_regex must be at most {MAX_COMMAND_REGEX_LENGTH} characters")
        return value


class ProcessInfo(BaseModel):
    processId: int = Field(..., description="The ID of the process")
    processString: str = Field(..., description="The complete string of the process")


def error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(code=status_code, message=message).model_dump(),
    )


def is_safe_regex(pattern: str) -> bool:
    if not pattern:
        return True

    nested_quantifier = re.compile(
        r"(?:\([^)]*[+*][^)]*\)|\[[^\]]+\][+*]|\\?.[+*])(?:[+*]|\{\d+(?:,\d*)?\})"
    )
    if nested_quantifier.search(pattern):
        return False

    alternation_count = pattern.count("|")
    if alternation_count > 8:
        return False

    group_count = pattern.count("(")
    if group_count > 12:
        return False

    meta_count = sum(1 for ch in pattern if ch in SAFE_REGEX_META_CHARS)
    if meta_count > 32:
        return False

    return True


def build_ps_command(filter_flags: Optional[str]) -> List[str]:
    command = ["ps"]

    if filter_flags:
        try:
            extra_args = shlex.split(filter_flags)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid filter_flags") from exc

        if len(extra_args) > MAX_PS_ARGS:
            raise HTTPException(status_code=400, detail="Invalid filter_flags")

        for arg in extra_args:
            if not arg:
                raise HTTPException(status_code=400, detail="Invalid filter_flags")
            if len(arg) > 32:
                raise HTTPException(status_code=400, detail="Invalid filter_flags")
            if "\x00" in arg or "\n" in arg or "\r" in arg:
                raise HTTPException(status_code=400, detail="Invalid filter_flags")
            command.append(arg)

    command.append("-o")
    command.append("pid=")
    command.append("-o")
    command.append("args=")

    return command


def parse_ps_output(output: str) -> List[ProcessInfo]:
    processes: List[ProcessInfo] = []

    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue

        stripped = line.lstrip()
        parts = stripped.split(None, 1)
        if not parts:
            continue

        try:
            pid = int(parts[0])
        except ValueError:
            continue

        process_string = parts[1] if len(parts) > 1 else ""
        processes.append(ProcessInfo(processId=pid, processString=process_string))

    return processes


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return error_response(exc.status_code, message)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(_: Request, __: RequestValidationError):
    return error_response(400, "Invalid request body")


@app.post(
    "/monitor/commands",
    response_model=List[ProcessInfo],
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    summary="Retrieve a list of running processes",
    description="Returns the list of processes running on the server whose command string matches a given regular expression.",
)
async def monitor_commands(request: MonitorRequest):
    if not is_safe_regex(request.command_regex):
        raise HTTPException(status_code=400, detail="Invalid command_regex")

    try:
        pattern = re.compile(request.command_regex)
    except re.error as exc:
        raise HTTPException(status_code=400, detail="Invalid command_regex") from exc

    ps_command = build_ps_command(request.filter_flags)

    try:
        completed = subprocess.run(
            ps_command,
            capture_output=True,
            text=True,
            check=False,
            timeout=PS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=500, detail="Process listing failed") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Process listing failed") from exc

    stdout = completed.stdout or ""
    if len(stdout.encode("utf-8", errors="ignore")) > MAX_PS_OUTPUT_BYTES:
        raise HTTPException(status_code=500, detail="Process listing failed")

    if completed.returncode != 0:
        raise HTTPException(status_code=500, detail="Process listing failed")

    processes = parse_ps_output(stdout)
    matched = [proc for proc in processes if pattern.search(proc.processString)]

    return matched


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)