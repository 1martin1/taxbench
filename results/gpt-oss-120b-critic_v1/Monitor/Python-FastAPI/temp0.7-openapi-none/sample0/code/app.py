import re
import shlex
import subprocess
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, validator

app = FastAPI(title="Monitor Service", version="1.0.0")


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class MonitorRequest(BaseModel):
    filter_flags: Optional[str] = Field(
        default="aux",
        description='Flags to filter and sort processes, matching the flags of the "ps" command',
        example="aux -T",
    )
    command_regex: str = Field(
        ...,
        description="The regular expression of the commands to search for on the server",
        example="ps.*",
    )

    @validator("filter_flags")
    def validate_filter_flags(cls, v: Optional[str]) -> str:
        """
        Very strict validation – only alphanumerics, hyphens, and spaces are allowed,
        and the total length is limited to avoid resource‑exhaustion attacks.
        """
        if v is None:
            return "aux"
        if len(v) > 100:
            raise ValueError("filter_flags is too long")
        if not re.fullmatch(r"[A-Za-z0-9\s\-]+", v):
            raise ValueError(
                "filter_flags contains illegal characters; only letters, numbers, spaces and hyphens are permitted"
            )
        return v

    @validator("command_regex")
    def validate_regex(cls, v: str) -> str:
        """
        Limit the size of the regular expression to mitigate ReDoS.
        """
        if len(v) > 200:
            raise ValueError("command_regex is too long")
        return v


class ProcessInfo(BaseModel):
    processId: int = Field(..., description="The ID of the process")
    processString: str = Field(..., description="The (sanitized) command string of the process")


# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #
_MAX_PS_TIMEOUT = 5  # seconds
_MAX_COMMAND_LENGTH = 200  # characters – truncate very long command lines


def _run_ps_command(flags: List[str]) -> List[str]:
    """
    Execute the ps command with a safe timeout and a predictable output format.
    The output is forced to contain only PID and full command line (args) to avoid
    fragile column parsing.
    """
    # Force a deterministic output: PID and full command line.
    # Adding "-o pid=,args=" after any user‑supplied flags guarantees the format.
    cmd = ["ps"] + flags + ["-o", "pid=", "-o", "args="]

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=_MAX_PS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=504,
            detail="ps command timed out",
        )
    except subprocess.CalledProcessError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to execute ps command: {exc}",
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail="ps command not found on this system.",
        )
    return completed.stdout.splitlines()


def _sanitize_command(command: str) -> str:
    """
    Reduce the amount of potentially sensitive data leaked by the API.
    Only the executable name (the first token) is retained, and the result
    is truncated to a reasonable length.
    """
    # Keep only the first whitespace‑separated token (the executable).
    first_token = command.strip().split()[0] if command.strip() else ""
    if len(first_token) > _MAX_COMMAND_LENGTH:
        first_token = first_token[: _MAX_COMMAND_LENGTH] + "..."
    return first_token


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #
@app.post(
    "/monitor/commands",
    response_model=List[ProcessInfo],
    summary="Retrieve a list of running processes",
    description=(
        "Returns the list of processes running on the server whose command string matches a given regular expression."
    ),
)
def monitor_commands(request: MonitorRequest):
    # ------------------------------------------------------------------- #
    # Build and validate the ps command flags
    # ------------------------------------------------------------------- #
    try:
        flags = shlex.split(request.filter_flags) if request.filter_flags else []
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid filter_flags: {e}")

    # ------------------------------------------------------------------- #
    # Run ps with a deterministic output format
    # ------------------------------------------------------------------- #
    lines = _run_ps_command(flags)

    if not lines:
        return []

    # No header is produced by the "-o pid=,args=" format, so all lines contain data.
    # ------------------------------------------------------------------- #
    # Compile the user supplied regular expression safely
    # ------------------------------------------------------------------- #
    try:
        # Use ASCII flag to avoid excessive backtracking on Unicode patterns.
        pattern = re.compile(request.command_regex, re.ASCII)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Invalid regular expression: {e}")

    results: List[ProcessInfo] = []

    for line in lines:
        # Expected format: "<pid> <command>"
        # Split only on the first whitespace to separate PID from the rest.
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue  # malformed line, skip

        pid_str, command_str = parts
        try:
            pid = int(pid_str)
        except ValueError:
            continue  # invalid PID, skip

        # Apply regex to the *full* command line (before sanitisation) but limit
        # the length examined to avoid pathological backtracking.
        if not pattern.search(command_str[:_MAX_COMMAND_LENGTH]):
            continue

        sanitized = _sanitize_command(command_str)

        results.append(ProcessInfo(processId=pid, processString=sanitized))

    return results


# --------------------------------------------------------------------------- #
# Application entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)