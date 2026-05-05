import re
import subprocess
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(
    title="Monitor Service",
    description="A service that accepts a filter criterion and returns a list of matching process IDs.",
    version="1.0.0",
)


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


class ErrorResponse(BaseModel):
    code: int = Field(..., description="Numeric error code")
    message: str = Field(..., description="Human-readable error message")


@app.post(
    "/monitor/commands",
    response_model=List[ProcessInfo],
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Retrieve a list of running processes",
    description=(
        "Returns the list of processes running on the server whose command string matches a given regular expression."
    ),
)
async def get_processes(request: MonitorRequest):
    """
    Execute `ps` to retrieve process list, filter by the supplied regular expression,
    and return matching processes.
    """
    # Build the base ps command. We use `pid,args` to get PID and the full command line.
    ps_cmd = ["ps", "-eo", "pid,args"]

    # If filter_flags are provided, attempt to split them and prepend to the command.
    # We ignore any flag that could interfere with the `-eo pid,args` part.
    if request.filter_flags:
        # Simple split on whitespace; this is not a full parser but sufficient for typical flags.
        extra_flags = request.filter_flags.strip().split()
        # Insert extra flags after "ps"
        ps_cmd = ["ps"] + extra_flags + ["-eo", "pid,args"]

    try:
        # Run the command and capture output as text.
        output = subprocess.check_output(ps_cmd, text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": 500, "message": f"Failed to execute ps command: {exc}"},
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail={"code": 500, "message": "ps command not found on this system."},
        )

    # Compile the regular expression once for efficiency.
    try:
        regex = re.compile(request.command_regex)
    except re.error as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": 400, "message": f"Invalid regular expression: {exc}"},
        )

    processes: List[ProcessInfo] = []

    lines = output.strip().splitlines()
    # The first line is the header (e.g., "PID COMMAND")
    for line in lines[1:]:
        # Split only on the first whitespace to separate PID and the rest of the command line.
        parts = line.strip().split(None, 1)
        if not parts:
            continue
        pid_str = parts[0]
        cmd_str = parts[1] if len(parts) > 1 else ""

        # Ensure PID is an integer.
        try:
            pid = int(pid_str)
        except ValueError:
            continue  # Skip malformed lines.

        # Apply the regex filter on the command string.
        if regex.search(cmd_str):
            processes.append(ProcessInfo(processId=pid, processString=cmd_str))

    return processes


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)