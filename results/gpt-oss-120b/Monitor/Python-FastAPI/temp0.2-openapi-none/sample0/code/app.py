import re
import shlex
import subprocess
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, ValidationError

app = FastAPI(title="Monitor Service", version="1.0.0")


class MonitorRequest(BaseModel):
    filter_flags: Optional[str] = Field(
        default=None,
        description='Flags to filter and sort processes, matching the flags of the "ps" command',
        example="aux -T",
    )
    command_regex: str = Field(
        ...,
        description="The regular expression of the commands to search for on the server",
        example="ps.*",
    )


class ProcessInfo(BaseModel):
    processId: int = Field(..., description="The ID of the process")
    processString: str = Field(..., description="The complete string of the process")


class ErrorResponse(BaseModel):
    code: int = Field(..., description="Numeric error code")
    message: str = Field(..., description="Human-readable error message")


def run_ps_command(filter_flags: Optional[str]) -> List[tuple[int, str]]:
    """
    Executes the `ps` command with optional flags and returns a list of (pid, command_string) tuples.
    """
    base_cmd = ["ps"]
    if filter_flags:
        # Split the flags safely; ignore empty strings.
        base_cmd += shlex.split(filter_flags)

    # Ensure we always request pid and full command line.
    # If the user already supplied a format, we still append to guarantee output.
    base_cmd += ["-eo", "pid,args"]

    try:
        completed = subprocess.run(
            base_cmd,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to execute ps command: {e}")

    lines = completed.stdout.strip().splitlines()
    # First line is header, skip it.
    result = []
    for line in lines[1:]:
        # ps output may have leading spaces; split max 1 time.
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            pid_str, cmd = parts
        elif len(parts) == 1:
            pid_str, cmd = parts[0], ""
        else:
            continue
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        result.append((pid, cmd))
    return result


@app.post(
    "/monitor/commands",
    response_model=List[ProcessInfo],
    responses={400: {"model": ErrorResponse}},
    summary="Retrieve a list of running processes",
    description=(
        "Returns the list of processes running on the server whose command string matches a given regular expression."
    ),
)
async def monitor_commands(request: MonitorRequest):
    # Compile the regex; return 400 if invalid.
    try:
        pattern = re.compile(request.command_regex)
    except re.error as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": 400, "message": f"Invalid regular expression: {exc}"},
        )

    try:
        processes = run_ps_command(request.filter_flags)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": 500, "message": str(exc)},
        )

    matched: List[ProcessInfo] = []
    for pid, cmd in processes:
        if pattern.search(cmd):
            matched.append(ProcessInfo(processId=pid, processString=cmd))

    return matched


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)