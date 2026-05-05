import os
import re
import subprocess
from typing import List, Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

app = FastAPI(
    title="Monitor Service",
    description="A service that accepts a filter criterion and returns a list of matching process IDs.",
    version="1.0.0",
)


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


class ErrorModel(BaseModel):
    code: int = Field(..., description="Numeric error code")
    message: str = Field(..., description="Human-readable error message")


# ---------------------------------------------------------------------------
# Custom exception to carry both HTTP status and the error model fields
# ---------------------------------------------------------------------------
class APIError(HTTPException):
    def __init__(self, status_code: int, code: int, message: str):
        super().__init__(status_code=status_code, detail=message)
        self.error_code = code
        self.error_message = message


@app.exception_handler(APIError)
async def api_error_handler(request: Request, exc: APIError):
    """Return errors exactly as defined by the OpenAPI Error schema."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.error_code, "message": exc.error_message},
    )


# ---------------------------------------------------------------------------
# Endpoint implementation
# ---------------------------------------------------------------------------
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
    # -----------------------------------------------------------------------
    # Validate and compile the regular expression
    # -----------------------------------------------------------------------
    try:
        regex = re.compile(request.command_regex)
    except re.error as exc:
        raise APIError(
            status_code=400,
            code=1001,
            message=f"Invalid regular expression: {exc}",
        )

    # -----------------------------------------------------------------------
    # Validate filter flags – allow only alphanumeric characters and hyphens
    # -----------------------------------------------------------------------
    ps_cmd = ["ps"]
    if request.filter_flags:
        flag_tokens = request.filter_flags.split()
        flag_pattern = re.compile(r"^[a-zA-Z0-9-]+$")

        for token in flag_tokens:
            if not flag_pattern.fullmatch(token):
                raise APIError(
                    status_code=400,
                    code=1003,
                    message=f"Invalid filter flag: {token}",
                )
        ps_cmd.extend(flag_tokens)

    # Ensure we always request PID and full command line in a portable way
    ps_cmd.extend(["-eo", "pid,command"])

    # -----------------------------------------------------------------------
    # Execute the ps command safely (no shell, arguments already validated)
    # -----------------------------------------------------------------------
    try:
        completed = subprocess.run(
            ps_cmd,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise APIError(
            status_code=500,
            code=1002,
            message=f"Failed to execute ps: {exc}",
        )
    except Exception as exc:
        # Catch unexpected errors (e.g., OSError) and report as internal error
        raise APIError(
            status_code=500,
            code=1004,
            message=f"Unexpected error while executing ps: {exc}",
        )

    # -----------------------------------------------------------------------
    # Parse the output
    # -----------------------------------------------------------------------
    lines = completed.stdout.strip().splitlines()
    if not lines:
        return []

    processes: List[ProcessInfo] = []
    # Skip the header line (usually "PID COMMAND")
    for line in lines[1:]:
        if not line.strip():
            continue
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


# ---------------------------------------------------------------------------
# Application entry point – respect PORT env var for flexibility
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "5000"))
    uvicorn.run(app, host="0.0.0.0", port=port)