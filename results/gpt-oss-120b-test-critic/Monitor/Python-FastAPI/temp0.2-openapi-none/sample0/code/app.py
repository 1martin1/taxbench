import re
import shlex
import subprocess
from typing import List, Optional, Tuple

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

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


def run_ps_command(filter_flags: Optional[str]) -> List[Tuple[int, str]]:
    """
    Executes the `ps` command with optional flags and returns a list of (pid, command_string) tuples.
    """
    base_cmd = ["ps"]
    if filter_flags:
        # Safely split user‑provided flags; shlex ensures no shell injection.
        base_cmd += shlex.split(filter_flags)

    # Force output of PID and full command line.
    base_cmd += ["-eo", "pid,args"]

    try:
        completed = subprocess.run(
            base_cmd,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        # Propagate a clear error for the caller to translate into a 400 response.
        raise RuntimeError(f"Failed to execute ps command: {e.stderr or e}")

    lines = completed.stdout.strip().splitlines()
    # Skip header line.
    result: List[Tuple[int, str]] = []
    for line in lines[1:]:
        parts = line.strip().split(None, 1)
        if not parts:
            continue
        pid_str = parts[0]
        cmd = parts[1] if len(parts) > 1 else ""
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        result.append((pid, cmd))
    return result


@app.post(
    "/monitor/commands",
    response_model=List[ProcessInfo],
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Retrieve a list of running processes",
    description=(
        "Returns the list of processes running on the server whose command string matches a given regular expression."
    ),
)
async def monitor_commands(request: MonitorRequest):
    # Basic protection against excessively complex regular expressions.
    MAX_REGEX_LENGTH = 100
    if len(request.command_regex) > MAX_REGEX_LENGTH:
        raise HTTPException(
            status_code=400,
            detail={"code": 400, "message": f"Regular expression exceeds maximum length of {MAX_REGEX_LENGTH} characters."},
        )

    # Compile the regex; return 400 if invalid.
    try:
        pattern = re.compile(request.command_regex)
    except re.error as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": 400, "message": f"Invalid regular expression: {exc}"},
        )

    # Run the blocking ps command in a thread pool to avoid blocking the event loop.
    try:
        processes = await run_in_threadpool(lambda: run_ps_command(request.filter_flags))
    except RuntimeError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": 400, "message": str(exc)},
        )
    except Exception as exc:
        # Unexpected errors are reported as server errors.
        raise HTTPException(
            status_code=500,
            detail={"code": 500, "message": f"Internal server error: {exc}"},
        )

    matched: List[ProcessInfo] = []
    for pid, cmd in processes:
        if pattern.search(cmd):
            matched.append(ProcessInfo(processId=pid, processString=cmd))

    return matched


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)