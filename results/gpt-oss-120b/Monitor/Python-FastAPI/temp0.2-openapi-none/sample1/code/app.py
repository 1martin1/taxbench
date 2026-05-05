import re
import subprocess
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(
    title="Monitor Service",
    description="A service that accepts a filter criterion and returns a list of matching process IDs.",
    version="1.0.0",
)


class MonitorRequest(BaseModel):
    filter_flags: Optional[str] = None
    command_regex: str


class ProcessInfo(BaseModel):
    processId: int
    processString: str


class ErrorModel(BaseModel):
    code: int
    message: str


@app.post(
    "/monitor/commands",
    response_model=List[ProcessInfo],
    responses={400: {"model": ErrorModel}, 500: {"model": ErrorModel}},
    summary="Retrieve a list of running processes",
    description=(
        "Returns the list of processes running on the server whose command string matches a given "
        "regular expression."
    ),
)
async def monitor_commands(request: MonitorRequest):
    # Compile the regular expression
    try:
        regex = re.compile(request.command_regex)
    except re.error as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": 1001, "message": f"Invalid regular expression: {exc}"},
        )

    # Build the ps command
    ps_cmd = ["ps"]
    if request.filter_flags:
        # Split on whitespace, ignore empty parts
        ps_cmd += [flag for flag in request.filter_flags.split() if flag]
    # Ensure we always request PID and full command line
    ps_cmd += ["-eo", "pid,command"]

    # Execute the command
    try:
        completed = subprocess.run(
            ps_cmd, capture_output=True, text=True, check=True
        )
    except subprocess.CalledProcessError as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": 1002, "message": f"Failed to execute ps: {exc}"},
        )

    lines = completed.stdout.strip().splitlines()
    if not lines:
        return []

    # Skip header line (usually "PID COMMAND")
    processes: List[ProcessInfo] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        # Split into PID and the rest (command string)
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        pid_str, cmd_str = parts
        try:
            pid = int(pid_str)
        except ValueError:
            continue

        if regex.search(cmd_str):
            processes.append(ProcessInfo(processId=pid, processString=cmd_str))

    return processes


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)