import re
import subprocess
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

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


def fetch_processes(filter_flags: Optional[str] = None) -> List[tuple[int, str]]:
    """
    Retrieve a list of (pid, command_string) tuples from the system.
    The `filter_flags` argument is ignored for safety; we always request pid and args.
    """
    try:
        # Use a safe, portable ps format: pid and the full command line (args)
        result = subprocess.check_output(
            ["ps", "-eo", "pid,args"], text=True, stderr=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Failed to execute ps command: {exc}")

    lines = result.strip().splitlines()
    # First line is the header, e.g., "PID COMMAND"
    process_entries = []
    for line in lines[1:]:
        if not line.strip():
            continue
        # Split only on the first whitespace to separate pid from the rest
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 1:
            pid_str, cmd_str = parts[0], ""
        else:
            pid_str, cmd_str = parts
        try:
            pid = int(pid_str)
        except ValueError:
            continue  # skip malformed lines
        process_entries.append((pid, cmd_str))
    return process_entries


@app.post(
    "/monitor/commands",
    response_model=List[ProcessInfo],
    responses={400: {"model": ErrorResponse}},
    summary="Retrieve a list of running processes",
    description=(
        "Returns the list of processes running on the server whose command string "
        "matches a given regular expression."
    ),
)
def monitor_commands(request: MonitorRequest):
    # Compile the regex; return 400 if invalid
    try:
        pattern = re.compile(request.command_regex)
    except re.error as e:
        raise HTTPException(
            status_code=400,
            detail={"code": 400, "message": f"Invalid regular expression: {e}"},
        )

    # Fetch processes (ignore filter_flags for simplicity and safety)
    try:
        processes = fetch_processes(request.filter_flags)
    except RuntimeError as e:
        raise HTTPException(
            status_code=500,
            detail={"code": 500, "message": str(e)},
        )

    matching = []
    for pid, cmd in processes:
        if pattern.search(cmd):
            matching.append(ProcessInfo(processId=pid, processString=cmd))

    return matching


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)