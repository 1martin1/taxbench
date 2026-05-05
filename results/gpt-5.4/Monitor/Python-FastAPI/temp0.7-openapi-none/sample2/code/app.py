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


class ProcessInfo(BaseModel):
    processId: int = Field(..., description="The ID of the process")
    processString: str = Field(..., description="The complete string of the process")


def _build_ps_command(filter_flags: Optional[str]) -> List[str]:
    command = ["ps"]

    if filter_flags:
        try:
            extra_args = shlex.split(filter_flags)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": 400, "message": f"Invalid filter_flags: {exc}"},
            )
        command.extend(extra_args)

    return command


def _extract_pid_and_line(line: str) -> Optional[ProcessInfo]:
    stripped = line.strip()
    if not stripped:
        return None

    match = re.match(r"^\s*(\d+)\s+(.*)$", line)
    if match:
        return ProcessInfo(processId=int(match.group(1)), processString=line.rstrip())

    tokens = stripped.split(None, 1)
    if not tokens:
        return None

    if tokens[0].isdigit():
        process_string = line.rstrip()
        return ProcessInfo(processId=int(tokens[0]), processString=process_string)

    return None


def _run_ps(filter_flags: Optional[str]) -> str:
    command = _build_ps_command(filter_flags)

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail={"code": 500, "message": "The 'ps' command is not available on this server."},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": 500, "message": f"Failed to execute 'ps': {exc}"},
        )

    if result.returncode != 0:
        stderr = (result.stderr or "").strip() or "Unknown error"
        raise HTTPException(
            status_code=400,
            detail={"code": 400, "message": f"'ps' command failed: {stderr}"},
        )

    return result.stdout


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail and "message" in detail:
        return JSONResponse(status_code=exc.status_code, content=detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "message": str(detail)},
    )


@app.post(
    "/monitor/commands",
    response_model=List[ProcessInfo],
    responses={
        400: {"model": ErrorResponse, "description": "Bad Request"},
        500: {"model": ErrorResponse, "description": "Internal Server Error"},
    },
    summary="Retrieve a list of running processes",
    description="Returns the list of processes running on the server whose command string matches a given regular expression.",
)
async def monitor_commands(request: MonitorRequest) -> List[ProcessInfo]:
    try:
        regex = re.compile(request.command_regex)
    except re.error as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": 400, "message": f"Invalid command_regex: {exc}"},
        )

    output = _run_ps(request.filter_flags)
    lines = output.splitlines()

    matched_processes: List[ProcessInfo] = []

    for line in lines:
        process_info = _extract_pid_and_line(line)
        if process_info is None:
            continue

        if regex.search(process_info.processString):
            matched_processes.append(process_info)

    return matched_processes


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)