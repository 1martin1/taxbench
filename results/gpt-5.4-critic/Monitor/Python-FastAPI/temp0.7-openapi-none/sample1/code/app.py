import asyncio
import os
import re
import shlex
import subprocess
import threading
from contextlib import asynccontextmanager
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


MAX_REGEX_LENGTH = 256
MAX_FILTER_FLAGS_LENGTH = 128
PS_TIMEOUT_SECONDS = 3.0
PS_MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_CONCURRENT_REQUESTS = 4


class ErrorResponse(BaseModel):
    code: int
    message: str


class MonitorRequest(BaseModel):
    filter_flags: Optional[str] = Field(
        default=None,
        description='Flags to filter and sort processes, matching the flags of the "ps" command',
        examples=["aux -T"],
        max_length=MAX_FILTER_FLAGS_LENGTH,
    )
    command_regex: str = Field(
        ...,
        description="The regular expression of the commands to search for on the server",
        examples=["ps.*"],
        min_length=1,
        max_length=MAX_REGEX_LENGTH,
    )


class ProcessEntry(BaseModel):
    processId: int
    processString: str


def parse_filter_flags(filter_flags: Optional[str]) -> List[str]:
    if not filter_flags:
        return []

    try:
        parts = shlex.split(filter_flags)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid filter_flags: {exc}") from exc

    return parts


def build_ps_command(filter_flags: Optional[str]) -> List[str]:
    flags = parse_filter_flags(filter_flags)
    return ["ps", *flags]


def validate_regex_safety(command_regex: str) -> re.Pattern[str]:
    nested_quantifier_patterns = (
        r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)[+*{]",
        r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)\?",
        r"\.\*[+*{]",
        r"\.\+[+*{]",
    )

    for unsafe_pattern in nested_quantifier_patterns:
        if re.search(unsafe_pattern, command_regex):
            raise HTTPException(
                status_code=400,
                detail="Invalid command_regex: potentially unsafe regex complexity",
            )

    try:
        return re.compile(command_regex)
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid command_regex: {exc}") from exc


def run_ps_with_limits(cmd: List[str]) -> str:
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail="Internal server error") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    stdout_chunks: List[bytes] = []
    stderr_chunks: List[bytes] = []
    stdout_size = 0
    stderr_size = 0

    def read_stream(stream, chunks: List[bytes], size_ref: List[int]) -> None:
        nonlocal stdout_size, stderr_size
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if size_ref is stdout_size_ref:
                    stdout_size += len(chunk)
                else:
                    stderr_size += len(chunk)
        finally:
            stream.close()

    stdout_size_ref = [0]
    stderr_size_ref = [0]

    stdout_thread = threading.Thread(target=read_stream, args=(process.stdout, stdout_chunks, stdout_size_ref))
    stderr_thread = threading.Thread(target=read_stream, args=(process.stderr, stderr_chunks, stderr_size_ref))
    stdout_thread.start()
    stderr_thread.start()

    try:
        process.wait(timeout=PS_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    stdout_thread.join(timeout=1)
    stderr_thread.join(timeout=1)

    total_output = sum(len(chunk) for chunk in stdout_chunks) + sum(len(chunk) for chunk in stderr_chunks)
    if total_output > PS_MAX_OUTPUT_BYTES:
        process.kill()
        raise HTTPException(status_code=500, detail="Internal server error")

    if process.returncode != 0:
        raise HTTPException(status_code=500, detail="Internal server error")

    return b"".join(stdout_chunks).decode("utf-8", errors="replace")


def parse_ps_output(output: str, pattern: re.Pattern[str]) -> List[ProcessEntry]:
    entries: List[ProcessEntry] = []

    lines = output.splitlines()
    for line in lines:
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

        process_string = stripped
        if pattern.search(process_string):
            entries.append(ProcessEntry(processId=pid, processString=process_string))

    return entries


def get_matching_processes(filter_flags: Optional[str], command_regex: str) -> List[ProcessEntry]:
    pattern = validate_regex_safety(command_regex)
    cmd = build_ps_command(filter_flags)
    output = run_ps_with_limits(cmd)
    return parse_ps_output(output, pattern)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.monitor_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    yield


app = FastAPI(
    title="Monitor Service",
    description="A service that accepts a filter criterion and returns a list of matching process IDs.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.post(
    "/monitor/commands",
    response_model=List[ProcessEntry],
    responses={
        400: {"model": ErrorResponse, "description": "Bad request"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
    summary="Retrieve a list of running processes",
    description="Returns the list of processes running on the server whose command string matches a given regular expression.",
)
async def monitor_commands(request: MonitorRequest) -> List[ProcessEntry]:
    async with app.state.monitor_semaphore:
        return await asyncio.to_thread(get_matching_processes, request.filter_flags, request.command_regex)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(code=exc.status_code, message=detail).model_dump(),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(code=422, message="Validation error").model_dump(),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(code=500, message="Internal server error").model_dump(),
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)