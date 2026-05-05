import re
import shlex
import subprocess
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
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


def _build_ps_command(filter_flags: Optional[str]) -> List[str]:
    command = ["ps"]

    if filter_flags:
        try:
            extra_args = shlex.split(filter_flags)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": 400, "message": f"Invalid filter_flags: {exc}"},
            ) from exc

        for arg in extra_args:
            if arg == "command" or arg.startswith("command=") or arg == "-o" or arg.startswith("-o"):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": 400,
                        "message": 'The "command" parameter is not supported on this OS.',
                    },
                )
        command.extend(extra_args)

    return command


def _run_ps(filter_flags: Optional[str]) -> List[str]:
    command = _build_ps_command(filter_flags)

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": 500, "message": "The ps command is not available on this server."},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": 500, "message": f"Failed to execute ps: {exc}"},
        ) from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip() or "ps command failed"
        raise HTTPException(
            status_code=400,
            detail={"code": 400, "message": stderr},
        )

    lines = [line.rstrip("\n") for line in completed.stdout.splitlines()]
    return lines


def _extract_pid(line: str) -> Optional[int]:
    stripped = line.strip()
    if not stripped:
        return None

    match = re.match(r"^\s*(\d+)\b", line)
    if match:
        return int(match.group(1))

    parts = stripped.split()
    for token in parts:
        if token.isdigit():
            return int(token)

    return None


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
def monitor_commands(request: MonitorRequest) -> List[ProcessInfo]:
    try:
        pattern = re.compile(request.command_regex)
    except re.error as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": 400, "message": f"Invalid command_regex: {exc}"},
        ) from exc

    lines = _run_ps(request.filter_flags)

    if not lines:
        return []

    data_lines = lines[1:] if len(lines) > 1 else []

    results: List[ProcessInfo] = []
    for line in data_lines:
        if not line.strip():
            continue
        if not pattern.search(line):
            continue

        pid = _extract_pid(line)
        if pid is None:
            continue

        results.append(ProcessInfo(processId=pid, processString=line))

    return results


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)