import re
import subprocess
from typing import List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

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


class APIError(Exception):
    """Custom exception to produce error responses that match the OpenAPI Error schema."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message


@app.exception_handler(APIError)
async def api_error_handler(request: Request, exc: APIError):
    """Return JSON matching the Error schema."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "message": exc.message},
    )


def _validate_filter_flags(flags: str) -> List[str]:
    """
    Validate and sanitize filter flags supplied by the client.

    - Allows only alphanumeric characters and leading hyphens (e.g., "-aux").
    - Limits the number of flags to a reasonable amount.
    - Rejects any flag that could interfere with the mandatory output format.
    """
    max_flags = 5
    flag_pattern = re.compile(r"^-?[a-zA-Z]+$")

    raw_flags = flags.strip().split()
    if len(raw_flags) > max_flags:
        raise APIError(400, f"Too many filter flags (maximum {max_flags}).")

    cleaned_flags: List[str] = []
    for flag in raw_flags:
        if not flag_pattern.fullmatch(flag):
            raise APIError(400, f"Invalid filter flag: '{flag}'.")
        # Disallow flags that modify the output format (e.g., -o, -O)
        if flag.lstrip("-").lower().startswith(("o", "O")):
            raise APIError(400, f"Filter flag '{flag}' is not allowed because it changes output format.")
        cleaned_flags.append(flag)

    return cleaned_flags


def _build_ps_command(filter_flags: Optional[str]) -> List[str]:
    """
    Construct the ps command safely, inserting validated filter flags.
    The command always ends with '-eo pid,args' to guarantee a predictable output.
    """
    base_cmd = ["ps"]
    if filter_flags:
        extra_flags = _validate_filter_flags(filter_flags)
        base_cmd.extend(extra_flags)
    base_cmd.extend(["-eo", "pid,args"])
    return base_cmd


async def _run_ps_command(cmd: List[str]) -> str:
    """
    Execute the ps command in a thread pool to avoid blocking the event loop.
    Returns the command output as a string.
    """
    try:
        output = await run_in_threadpool(
            lambda: subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
        )
        return output
    except subprocess.CalledProcessError as exc:
        # Treat command execution errors as client errors because they stem from invalid flags.
        raise APIError(400, f"Failed to execute ps command: {exc.output.strip()}")
    except FileNotFoundError:
        raise APIError(500, "ps command not found on this system.")
    except Exception as exc:
        # Unexpected errors are reported as server errors.
        raise APIError(500, f"Unexpected error while executing ps: {str(exc)}")


def _compile_regex(pattern: str) -> re.Pattern:
    """
    Compile the user‑provided regular expression with safety checks.
    - Limits the length of the pattern to prevent excessive resource consumption.
    """
    max_length = 200
    if len(pattern) > max_length:
        raise APIError(400, f"Regular expression is too long (maximum {max_length} characters).")
    try:
        compiled = re.compile(pattern)
        return compiled
    except re.error as exc:
        raise APIError(400, f"Invalid regular expression: {exc}")


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
    Execute `ps` to retrieve the process list, filter by the supplied regular expression,
    and return matching processes.
    """
    # Build a safe ps command.
    ps_cmd = _build_ps_command(request.filter_flags)

    # Run the command without blocking the event loop.
    output = await _run_ps_command(ps_cmd)

    # Compile the regex safely.
    regex = _compile_regex(request.command_regex)

    processes: List[ProcessInfo] = []

    lines = output.strip().splitlines()
    # Skip the header line (first line).
    for line in lines[1:]:
        parts = line.strip().split(None, 1)
        if not parts:
            continue
        pid_str = parts[0]
        cmd_str = parts[1] if len(parts) > 1 else ""

        try:
            pid = int(pid_str)
        except ValueError:
            continue  # Skip malformed lines.

        if regex.search(cmd_str):
            processes.append(ProcessInfo(processId=pid, processString=cmd_str))

    return processes


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)