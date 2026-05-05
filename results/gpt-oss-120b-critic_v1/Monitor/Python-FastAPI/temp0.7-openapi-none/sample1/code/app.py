import re
import shlex
import subprocess
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import uvicorn

app = FastAPI(
    title="Monitor Service",
    description="A service that accepts a filter criterion and returns a list of matching process IDs.",
    version="1.0.0",
)

# ---- Constants for security limits ----
MAX_REGEX_LENGTH = 200          # Prevent overly complex patterns
MAX_RESULTS = 1000              # Upper bound on number of processes returned
PS_TIMEOUT_SECONDS = 5         # Avoid hanging subprocesses
ALLOWED_FLAGS = {
    "a", "u", "x", "A", "e", "f", "l", "w", "w", "h", "H", "t", "T", "c", "C", "U", "G",
    "-a", "-u", "-x", "-A", "-e", "-f", "-l", "-w", "-h", "-H", "-t", "-T", "-c", "-C",
    "-U", "-G", "aux", "auxw", "au", "ux", "ax"
}


class MonitorRequest(BaseModel):
    filter_flags: Optional[str] = Field(
        default="",
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


def _sanitize_flags(flag_str: str) -> List[str]:
    """
    Validate and sanitize the user‑provided flag string.
    Only whitelisted flags are kept; unknown flags cause a 400 error.
    """
    if not flag_str:
        return []

    raw_flags = shlex.split(flag_str)
    clean_flags: List[str] = []

    for flag in raw_flags:
        # Accept combined short flags like "aux" as a whole token
        if flag in ALLOWED_FLAGS:
            clean_flags.append(flag)
            continue

        # Split combined short flags (e.g., "aux" -> "a", "u", "x")
        # Only keep them if each part is allowed
        if all(ch in ALLOWED_FLAGS for ch in flag):
            clean_flags.extend(list(flag))
            continue

        raise HTTPException(
            status_code=400,
            detail=f"Disallowed ps flag detected: '{flag}'. Allowed flags are limited for security.",
        )
    return clean_flags


@app.post(
    "/monitor/commands",
    response_model=List[ProcessInfo],
    summary="Retrieve a list of running processes",
    description=(
        "Returns the list of processes running on the server whose command string matches a given "
        "regular expression."
    ),
)
async def get_processes(request: MonitorRequest) -> List[ProcessInfo]:
    # ---- Validate regex ----
    if len(request.command_regex) > MAX_REGEX_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Regular expression exceeds maximum allowed length of {MAX_REGEX_LENGTH} characters.",
        )
    try:
        pattern = re.compile(request.command_regex)
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid regular expression: {exc}") from exc

    # ---- Sanitize filter flags ----
    try:
        safe_flags = _sanitize_flags(request.filter_flags)
    except HTTPException:
        raise  # re‑raise validation error

    # Build the ps command safely
    ps_cmd = ["ps"]
    if safe_flags:
        ps_cmd.extend(safe_flags)
    # Always request pid and full argument list; suppress header to simplify parsing
    ps_cmd.extend(["-eo", "pid,args", "--no-headers"])

    try:
        completed = subprocess.run(
            ps_cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=PS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(
            status_code=504,
            detail="ps command timed out.",
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to execute ps command: {exc}",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error while running ps: {exc}",
        ) from exc

    lines = completed.stdout.strip().splitlines()
    if not lines:
        return []

    result: List[ProcessInfo] = []
    for line in lines:
        # Stop processing if we already hit the safe limit
        if len(result) >= MAX_RESULTS:
            break

        # Expected format: PID <space> COMMAND...
        parts = line.strip().split(None, 1)
        if not parts:
            continue
        pid_str = parts[0]
        cmd_str = parts[1] if len(parts) > 1 else ""

        try:
            pid = int(pid_str)
        except ValueError:
            continue  # malformed line, skip

        if pattern.search(cmd_str):
            result.append(ProcessInfo(processId=pid, processString=cmd_str))

    return result


if __name__ == "__main__":
    # Run the server on 0.0.0.0:5000
    uvicorn.run(app, host="0.0.0.0", port=5000)