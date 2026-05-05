import re
import shlex
import subprocess
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

app = FastAPI(title="Monitor Service", version="1.0.0")


class ErrorResponse(BaseModel):
    code: int = Field(..., description="Numeric error code")
    message: str = Field(..., description="Human-readable error message")


class MonitorRequest(BaseModel):
    filter_flags: Optional[str] = Field(
        None,
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


# --------------------------------------------------------------------------- #
# Exception handling – return errors in the shape defined by ErrorResponse   #
# --------------------------------------------------------------------------- #
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Convert HTTPException into the ErrorResponse schema."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "message": exc.detail},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Catch‑all handler for unexpected errors."""
    return JSONResponse(
        status_code=500,
        content={"code": 500, "message": "Internal server error"},
    )


# --------------------------------------------------------------------------- #
# Helper functions                                                          #
# --------------------------------------------------------------------------- #
# Allow only alphanumeric characters, hyphens, underscores and spaces in
# filter flags to avoid command injection.
_FILTER_FLAGS_RE = re.compile(r"^[\w\s-]*$")

# Limit the size of a regular expression to mitigate ReDoS attacks.
_MAX_REGEX_LENGTH = 200

# Upper bound on the number of lines we will process from `ps` output.
_MAX_PS_LINES = 10_000

# Timeout (seconds) for the `ps` subprocess.
_PS_TIMEOUT = 5


def _run_ps(filter_flags: Optional[str]) -> List[str]:
    """
    Execute the `ps` command with optional flags and return its stdout lines.
    The output is limited to a maximum number of lines and the subprocess
    is bounded by a timeout to avoid hanging.
    """
    cmd = ["ps"]
    if filter_flags:
        # Validate filter flags before using them.
        if not _FILTER_FLAGS_RE.fullmatch(filter_flags):
            raise HTTPException(
                status_code=400,
                detail="filter_flags contain unsupported characters",
            )
        # shlex.split respects quoting while preventing shell injection.
        cmd.extend(shlex.split(filter_flags))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_PS_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=500,
            detail="ps command timed out",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to execute ps command: {exc}",
        )

    if result.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"ps command exited with code {result.returncode}: {result.stderr}",
        )

    # Split output into lines and enforce a hard limit.
    lines = result.stdout.splitlines()
    if len(lines) > _MAX_PS_LINES:
        lines = lines[:_MAX_PS_LINES]
    return lines


def _parse_ps_output(lines: List[str]) -> List[tuple[int, str]]:
    """Parse ps output lines into (pid, command_string) tuples."""
    processes = []
    for line in lines:
        # Skip header lines that contain column titles like PID, USER, etc.
        if re.search(r"\bPID\b", line):
            continue

        parts = line.split()
        pid_index: Optional[int] = None
        for idx, part in enumerate(parts):
            if part.isdigit():
                pid_index = idx
                break
        if pid_index is None:
            continue

        try:
            pid = int(parts[pid_index])
        except ValueError:
            continue

        pid_str = parts[pid_index]
        match = re.search(rf"\b{re.escape(pid_str)}\b", line)
        if not match:
            continue
        command_start = match.end()
        command = line[command_start:].strip()
        processes.append((pid, command))
    return processes


# --------------------------------------------------------------------------- #
# Endpoint                                                                  #
# --------------------------------------------------------------------------- #
@app.post(
    "/monitor/commands",
    response_model=List[ProcessInfo],
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Retrieve a list of running processes",
    description=(
        "Returns the list of processes running on the server whose command string "
        "matches a given regular expression."
    ),
)
async def monitor_processes(request: MonitorRequest):
    # Validate the regular expression length to guard against ReDoS.
    if len(request.command_regex) > _MAX_REGEX_LENGTH:
        raise HTTPException(
            status_code=400,
            detail="Regular expression is too long",
        )

    # Compile the regex early to validate it.
    try:
        regex = re.compile(request.command_regex)
    except re.error as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid regular expression: {exc}",
        )

    ps_lines = _run_ps(request.filter_flags)
    parsed = _parse_ps_output(ps_lines)

    matched: List[ProcessInfo] = []
    for pid, cmd_str in parsed:
        if regex.search(cmd_str):
            matched.append(ProcessInfo(processId=pid, processString=cmd_str))

    return matched


# --------------------------------------------------------------------------- #
# Application entry point                                                    #
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)