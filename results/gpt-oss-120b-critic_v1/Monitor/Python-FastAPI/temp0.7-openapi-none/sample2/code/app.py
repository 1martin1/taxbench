import os
import re
import logging
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# ----------------------------------------------------------------------
# Configuration & Constants
# ----------------------------------------------------------------------
MAX_REGEX_LENGTH = 200          # Prevent overly complex patterns
MAX_RESULTS = 1000              # Upper bound on returned processes
PROC_ROOT = "/proc"

# Set up a basic logger (stdout)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# FastAPI App Definition
# ----------------------------------------------------------------------
app = FastAPI(
    title="Monitor Service",
    description="A service that accepts a filter criterion and returns a list of matching process IDs.",
    version="1.0.0",
)

# ----------------------------------------------------------------------
# Pydantic Models
# ----------------------------------------------------------------------
class MonitorRequest(BaseModel):
    filter_flags: Optional[
        str
    ] = Field(
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


# ----------------------------------------------------------------------
# Helper Functions
# ----------------------------------------------------------------------
def _apply_filter_flags(
    processes: List[ProcessInfo], flags: Optional[str]
) -> List[ProcessInfo]:
    """
    Very lightweight interpretation of ``filter_flags``.
    Supported simple behaviours:
        - ``pid``   : sort by process ID (ascending)
        - ``cmd``   : sort by command string (case‑insensitive)
        - ``-r`` or ``reverse`` : reverse the final ordering
    Any unrecognised flag is ignored to keep compatibility.
    """
    if not flags:
        return processes

    # Normalise flags string for easier checks
    normalized = flags.lower()

    # Sorting
    if "pid" in normalized:
        processes.sort(key=lambda p: p.processId)
    elif "cmd" in normalized:
        processes.sort(key=lambda p: p.processString.lower())

    # Reversal
    if "-r" in normalized or "reverse" in normalized:
        processes.reverse()

    return processes


def _list_matching_processes(regex: str) -> List[ProcessInfo]:
    """
    Scan /proc for processes whose command line matches the supplied regular expression.
    The function respects ``MAX_RESULTS`` to avoid unbounded memory consumption.
    """
    # Compile the regex safely – caller validates length and syntax.
    pattern = re.compile(regex)

    matches: List[ProcessInfo] = []

    if not os.path.isdir(PROC_ROOT):
        raise RuntimeError("/proc filesystem not found; cannot enumerate processes.")

    # Iterate over numeric entries in /proc
    for entry in os.listdir(PROC_ROOT):
        if not entry.isdigit():
            continue

        pid = int(entry)
        pid_path = os.path.join(PROC_ROOT, entry)

        try:
            # Prefer the full command line; fall back to the comm file if empty.
            cmdline_path = os.path.join(pid_path, "cmdline")
            with open(cmdline_path, "rb") as f:
                raw = f.read()

            if raw:
                cmd = raw.replace(b"\0", b" ").decode(errors="ignore").strip()
            else:
                comm_path = os.path.join(pid_path, "comm")
                with open(comm_path, "r", encoding="utf-8", errors="ignore") as f:
                    cmd = f.read().strip()

            if pattern.search(cmd):
                matches.append(ProcessInfo(processId=pid, processString=cmd))
                if len(matches) >= MAX_RESULTS:
                    logger.info(
                        "Reached maximum result limit (%d); stopping enumeration.", MAX_RESULTS
                    )
                    break

        except (FileNotFoundError, PermissionError):
            # Process may have terminated or be inaccessible; skip silently.
            continue
        except Exception as exc:
            # Log unexpected errors but continue processing other entries.
            logger.exception("Unexpected error while processing PID %s: %s", pid, exc)
            continue

    return matches


# ----------------------------------------------------------------------
# Endpoint
# ----------------------------------------------------------------------
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
async def monitor_commands(request: MonitorRequest):
    """
    Return processes whose command line matches ``command_regex``.
    The optional ``filter_flags`` influences ordering as described in
    ``_apply_filter_flags``.
    """
    # Basic validation for regex length to mitigate ReDoS risk.
    if len(request.command_regex) > MAX_REGEX_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=400,
                message=f"Regular expression exceeds maximum length of {MAX_REGEX_LENGTH} characters.",
            ).dict(),
        )

    try:
        # Validate the regex syntax early.
        re.compile(request.command_regex)
    except re.error as regex_err:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=400, message=f"Invalid regular expression: {regex_err}"
            ).dict(),
        )

    try:
        raw_results = _list_matching_processes(request.command_regex)
        sorted_results = _apply_filter_flags(raw_results, request.filter_flags)
        return sorted_results
    except RuntimeError as rt_err:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(code=400, message=str(rt_err)).dict(),
        )
    except Exception:
        # Hide internal details from the client.
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=500, message="Internal server error."
            ).dict(),
        )


# ----------------------------------------------------------------------
# Application Entry Point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)