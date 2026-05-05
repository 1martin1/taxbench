import asyncio
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


# --------------------------------------------------------------------------- #
# Helper utilities
# --------------------------------------------------------------------------- #
_MAX_FILTER_FLAGS_LENGTH = 100  # characters
_MAX_FILTER_TOKENS = 10
_ALLOWED_FILTER_CHARS_REGEX = re.compile(r"^[a-zA-Z0-9\-\s_]+$")

_MAX_REGEX_LENGTH = 200  # characters


def _validate_filter_flags(flags: Optional[str]) -> List[str]:
    """
    Validate and sanitize the ``filter_flags`` string supplied by the client.
    Returns a list of safe flag tokens suitable for passing to ``ps``.
    """
    if not flags:
        return ["aux"]  # sensible default

    flags = flags.strip()
    if len(flags) > _MAX_FILTER_FLAGS_LENGTH:
        raise HTTPException(
            status_code=400,
            detail="filter_flags is too long",
        )

    if not _ALLOWED_FILTER_CHARS_REGEX.fullmatch(flags):
        raise HTTPException(
            status_code=400,
            detail="filter_flags contains invalid characters",
        )

    tokens = flags.split()
    if len(tokens) > _MAX_FILTER_TOKENS:
        raise HTTPException(
            status_code=400,
            detail="Too many filter flags supplied",
        )

    return tokens


def _validate_regex(pattern: str) -> re.Pattern:
    """
    Validate the regular expression supplied by the client.
    Limits length to mitigate ReDoS attacks.
    """
    if len(pattern) > _MAX_REGEX_LENGTH:
        raise HTTPException(
            status_code=400,
            detail="command_regex is too long",
        )
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid regular expression: {exc}",
        )
    return compiled


async def _run_ps(flags: List[str]) -> str:
    """
    Execute ``ps`` with the given flags asynchronously.
    Returns the stdout as a string, decoding with ``errors='replace'`` to avoid
    UnicodeDecodeError.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "ps",
            *flags,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail="ps command not found on this system.",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start ps command: {exc}",
        )

    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        err_msg = stderr.decode(errors="replace").strip()
        raise HTTPException(
            status_code=500,
            detail=f"ps command failed: {err_msg}",
        )

    return stdout.decode(errors="replace")


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
async def monitor_commands(request: MonitorRequest) -> List[ProcessInfo]:
    """
    Execute ``ps`` with optional user‑provided flags, then filter the output
    using a regular expression supplied by the client. Returns a list of matching
    process IDs together with the full process line.
    """
    # Validate inputs
    regex = _validate_regex(request.command_regex)
    flags = _validate_filter_flags(request.filter_flags)

    # Run ps asynchronously
    output = await _run_ps(flags)

    lines = output.strip().splitlines()
    if not lines:
        return []

    # Skip the header line (first line)
    data_lines = lines[1:]

    results: List[ProcessInfo] = []
    for line in data_lines:
        if not regex.search(line):
            continue

        # Split the line into at most 11 parts so the COMMAND column stays intact.
        parts = line.split(None, 10)
        if len(parts) < 2:
            continue  # malformed line

        pid_str = parts[1]
        try:
            pid = int(pid_str)
        except ValueError:
            continue  # PID not an integer

        results.append(ProcessInfo(processId=pid, processString=line))

    return results


# --------------------------------------------------------------------------- #
# Application entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)