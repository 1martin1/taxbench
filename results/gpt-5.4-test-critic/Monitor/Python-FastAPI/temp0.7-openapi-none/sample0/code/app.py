import os
import re
import shlex
import subprocess
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field


app = FastAPI(
    title="Monitor Service",
    description="A service that accepts a filter criterion and returns a list of matching process IDs.",
    version="1.0.0",
)


class ErrorModel(BaseModel):
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


class ProcessInfo(BaseModel):
    processId: int = Field(..., description="The ID of the process")
    processString: str = Field(..., description="The complete string of the process")


def error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorModel(code=status_code, message=message).model_dump(),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException):
    message = str(exc.detail) if exc.detail is not None else "Request failed"
    return error_response(exc.status_code, message)


def _build_ps_command(filter_flags: Optional[str]) -> List[str]:
    command = ["ps"]

    if filter_flags:
        try:
            extra_args = shlex.split(filter_flags)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid filter_flags: {exc}") from exc

        for arg in extra_args:
            if arg == "command" or arg.startswith("command="):
                raise HTTPException(
                    status_code=400,
                    detail='The "command" parameter is not supported on this OS.',
                )
            command.append(arg)

    return command


def _run_ps(filter_flags: Optional[str]) -> List[str]:
    command = _build_ps_command(filter_flags)

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            env=os.environ.copy(),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail="The ps command is not available on this server.") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to execute ps: {exc}") from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        message = stderr or "ps command failed"
        if "command" in message.lower():
            raise HTTPException(
                status_code=400,
                detail='The "command" parameter is not supported on this OS.',
            )
        raise HTTPException(status_code=400, detail=message)

    output = completed.stdout.splitlines()
    return output


def _extract_pid_and_line(line: str) -> Optional[ProcessInfo]:
    stripped = line.strip()
    if not stripped:
        return None

    match = re.match(r"^\s*(\d+)\s+(.*)$", line)
    if match:
        return ProcessInfo(processId=int(match.group(1)), processString=line.rstrip())

    columns = stripped.split()
    if not columns:
        return None

    pid_index = None
    for idx, value in enumerate(columns):
        if value.isdigit():
            pid_index = idx
            break

    if pid_index is None:
        return None

    return ProcessInfo(processId=int(columns[pid_index]), processString=line.rstrip())


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
async def monitor_commands(request: MonitorRequest):
    try:
        pattern = re.compile(request.command_regex)
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid command_regex: {exc}") from exc

    lines = _run_ps(request.filter_flags)
    results: List[ProcessInfo] = []

    for line in lines:
        if not pattern.search(line):
            continue

        process_info = _extract_pid_and_line(line)
        if process_info is not None:
            results.append(process_info)

    return results


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)