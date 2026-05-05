import os
import re
import shlex
import subprocess
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict


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
        min_length=1,
    )


class ProcessInfo(BaseModel):
    processId: int = Field(..., description="The ID of the process")
    processString: str = Field(..., description="The complete string of the process")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(code=422, message="Invalid request body").model_dump(),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(code=exc.status_code, message=detail).model_dump(),
    )


def _parse_filter_flags(filter_flags: Optional[str]) -> List[str]:
    if not filter_flags:
        return []

    try:
        parts = shlex.split(filter_flags)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid filter_flags: {exc}") from exc

    for part in parts:
        if not part.startswith("-") and not re.fullmatch(r"[A-Za-z]+", part):
            raise HTTPException(status_code=400, detail=f"Unsupported filter flag token: {part}")
        if ";" in part or "|" in part or "&" in part or "\n" in part or "\r" in part:
            raise HTTPException(status_code=400, detail="Invalid characters in filter_flags")

    return parts


def _run_ps(filter_flags: Optional[str]) -> List[str]:
    args = ["ps"]
    args.extend(_parse_filter_flags(filter_flags))
    args.extend(["-o", "pid=", "-o", "args="])

    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "LC_ALL": "C", "LANG": "C"},
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail="ps command is not available on this server") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to execute ps: {exc}") from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        message = stderr or "ps command failed"
        raise HTTPException(status_code=400, detail=message)

    lines = [line.rstrip("\n") for line in completed.stdout.splitlines() if line.strip()]
    return lines


def _extract_processes(lines: List[str], command_regex: str) -> List[ProcessInfo]:
    try:
        pattern = re.compile(command_regex)
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid command_regex: {exc}") from exc

    results: List[ProcessInfo] = []
    for line in lines:
        stripped = line.strip()
        match = re.match(r"^(\d+)\s+(.*)$", stripped)
        if not match:
            continue

        pid = int(match.group(1))
        process_string = match.group(2)

        if pattern.search(process_string):
            results.append(ProcessInfo(processId=pid, processString=stripped))

    return results


@app.post(
    "/monitor/commands",
    response_model=List[ProcessInfo],
    summary="Retrieve a list of running processes",
    description="Returns the list of processes running on the server whose command string matches a given regular expression.",
    responses={
        400: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def monitor_commands(payload: MonitorRequest) -> List[ProcessInfo]:
    lines = _run_ps(payload.filter_flags)
    return _extract_processes(lines, payload.command_regex)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)