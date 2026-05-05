import os
import re
import shlex
import subprocess
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
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


class ProcessItem(BaseModel):
    processId: int = Field(..., description="The ID of the process")
    processString: str = Field(..., description="The complete string of the process")


def error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(code=status_code, message=message).model_dump(),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response(422, "Invalid request body")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return error_response(exc.status_code, message)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return error_response(500, "Internal server error")


def build_ps_command(filter_flags: Optional[str]) -> List[str]:
    command = ["ps"]

    if filter_flags:
        try:
            extra_args = shlex.split(filter_flags)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid filter_flags")

        for arg in extra_args:
            if "\x00" in arg:
                raise HTTPException(status_code=400, detail="Invalid filter_flags")
            command.append(arg)

    return command


def extract_pid_and_line(line: str) -> Optional[ProcessItem]:
    stripped = line.rstrip("\n")
    if not stripped.strip():
        return None

    match = re.match(r"^\s*(\d+)\s+(.*)$", stripped)
    if match:
        return ProcessItem(processId=int(match.group(1)), processString=stripped)

    tokens = stripped.split()
    if len(tokens) >= 2:
        for token in tokens:
            if token.isdigit():
                return ProcessItem(processId=int(token), processString=stripped)

    return None


def run_ps(filter_flags: Optional[str]) -> List[ProcessItem]:
    command = build_ps_command(filter_flags)

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            env=os.environ.copy(),
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="ps command is not available")
    except OSError:
        raise HTTPException(status_code=500, detail="Failed to execute ps command")

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        if stderr:
            raise HTTPException(status_code=400, detail=f"ps command failed: {stderr}")
        raise HTTPException(status_code=400, detail="ps command failed")

    lines = completed.stdout.splitlines()
    if not lines:
        return []

    process_items: List[ProcessItem] = []
    for line in lines[1:]:
        item = extract_pid_and_line(line)
        if item is not None:
            process_items.append(item)

    return process_items


@app.post(
    "/monitor/commands",
    response_model=List[ProcessItem],
    responses={
        400: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    summary="Retrieve a list of running processes",
    description="Returns the list of processes running on the server whose command string matches a given regular expression.",
)
async def monitor_commands(payload: MonitorRequest) -> List[ProcessItem]:
    try:
        regex = re.compile(payload.command_regex)
    except re.error:
        raise HTTPException(status_code=400, detail="Invalid command_regex")

    processes = run_ps(payload.filter_flags)
    matched = [proc for proc in processes if regex.search(proc.processString)]
    return matched


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)