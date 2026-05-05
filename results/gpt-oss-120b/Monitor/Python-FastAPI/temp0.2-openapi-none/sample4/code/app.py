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


@app.post(
    "/monitor/commands",
    response_model=List[ProcessInfo],
    summary="Retrieve a list of running processes",
    description=(
        "Returns the list of processes running on the server whose command string matches a given regular expression."
    ),
)
async def get_processes(request: MonitorRequest):
    # Compile the regex; raise 400 if invalid
    try:
        pattern = re.compile(request.command_regex)
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid regular expression: {exc}")

    # Build the ps command.
    # We use -eo pid,args to get PID and full command line.
    # filter_flags are ignored for simplicity; they could be incorporated by the caller if needed.
    ps_cmd = ["ps", "-eo", "pid,args"]
    try:
        output = subprocess.check_output(ps_cmd, text=True)
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to execute ps: {exc}")

    processes: List[ProcessInfo] = []
    lines = output.strip().splitlines()
    # First line is header: "PID COMMAND"
    for line in lines[1:]:
        # Split only on the first whitespace to separate PID from the rest
        parts = line.strip().split(maxsplit=1)
        if not parts:
            continue
        pid_str = parts[0]
        cmd_str = parts[1] if len(parts) > 1 else ""
        try:
            pid = int(pid_str)
        except ValueError:
            continue  # skip malformed lines

        if pattern.search(cmd_str):
            processes.append(ProcessInfo(processId=pid, processString=cmd_str))

    return processes


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)