import os
import re
import shlex
import signal
import subprocess
from typing import Any, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


MAX_FILTER_FLAGS_LENGTH = 128
MAX_COMMAND_REGEX_LENGTH = 256
MAX_FILTER_FLAG_TOKENS = 16
MAX_PS_OUTPUT_BYTES = 2 * 1024 * 1024
PS_TIMEOUT_SECONDS = 5


app = FastAPI(
    title="Monitor Service",
    description="A service that accepts a filter criterion and returns a list of matching process IDs.",
    version="1.0.0",
)


class ErrorModel(BaseModel):
    code: int = Field(..., description="Numeric error code")
    message: str = Field(..., description="Human-readable error message")


class MonitorRequest(BaseModel):
    filter_flags: Optional[str] = Field(
        default=None,
        description='Flags to filter and sort processes, matching the flags of the "ps" command',
        examples=["aux -T"],
    )
    command_regex: str = Field(
        ...,
        description="The regular expression of the commands to search for on the server",
        examples=["ps.*"],
    )


class ProcessEntry(BaseModel):
    processId: int = Field(..., description="The ID of the process")
    processString: str = Field(..., description="The complete string of the process")


class APIError(Exception):
    def __init__(self, status_code: int, code: int, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


@app.exception_handler(APIError)
async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorModel(code=exc.code, message=exc.message).model_dump(),
    )


def _validate_request_fields(request: MonitorRequest) -> None:
    if len(request.command_regex) > MAX_COMMAND_REGEX_LENGTH:
        raise APIError(status_code=400, code=400, message="command_regex is too long.")

    if request.filter_flags is not None and len(request.filter_flags) > MAX_FILTER_FLAGS_LENGTH:
        raise APIError(status_code=400, code=400, message="filter_flags is too long.")


def _parse_filter_flags(filter_flags: Optional[str]) -> List[str]:
    if not filter_flags:
        return []

    try:
        parsed = shlex.split(filter_flags)
    except ValueError:
        raise APIError(status_code=400, code=400, message="Invalid filter_flags.")

    if len(parsed) > MAX_FILTER_FLAG_TOKENS:
        raise APIError(status_code=400, code=400, message="Too many filter_flags tokens.")

    for token in parsed:
        if len(token) > 64:
            raise APIError(status_code=400, code=400, message="A filter_flags token is too long.")
        if "\x00" in token:
            raise APIError(status_code=400, code=400, message="Invalid filter_flags.")

    return parsed


def _safe_compile_regex(pattern: str) -> re.Pattern[str]:
    try:
        compiled = re.compile(pattern)
    except re.error:
        raise APIError(status_code=400, code=400, message="Invalid command_regex.")

    nested_quantifier_patterns = [
        r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)[+*{]",
        r"\((?:[^()\\]|\\.)*\{[^{}]+\}(?:[^()\\]|\\.)*\)[+*{]",
        r"\.\*[+*{]",
        r"\.\++",
    ]
    for suspect in nested_quantifier_patterns:
        if re.search(suspect, pattern):
            raise APIError(status_code=400, code=400, message="command_regex is too complex.")

    return compiled


def _terminate_process(proc: subprocess.Popen[Any]) -> None:
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.communicate(timeout=1)
    except Exception:
        pass


def _run_ps(filter_flags: Optional[str]) -> str:
    extra_args = _parse_filter_flags(filter_flags)
    cmd = ["ps"] + extra_args + ["-o", "pid=", "-o", "args="]

    env = {"LC_ALL": "C", "LANG": "C"}
    if "PATH" in os.environ:
        env["PATH"] = os.environ["PATH"]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            env=env,
            preexec_fn=os.setsid if hasattr(os, "setsid") else None,
        )
    except FileNotFoundError:
        raise APIError(status_code=500, code=500, message="The ps command is not available on this server.")
    except Exception:
        raise APIError(status_code=500, code=500, message="Failed to execute ps.")

    try:
        stdout_data, stderr_data = proc.communicate(timeout=PS_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        if hasattr(os, "killpg") and hasattr(os, "getpgid"):
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                _terminate_process(proc)
        else:
            _terminate_process(proc)
        raise APIError(status_code=500, code=500, message="Process listing timed out.")
    except Exception:
        _terminate_process(proc)
        raise APIError(status_code=500, code=500, message="Failed to execute ps.")

    if len(stdout_data) > MAX_PS_OUTPUT_BYTES:
        raise APIError(status_code=500, code=500, message="Process listing output is too large.")

    if proc.returncode != 0:
        raise APIError(status_code=400, code=400, message="Invalid or unsupported filter_flags.")

    try:
        return stdout_data.decode("utf-8", errors="replace")
    except Exception:
        raise APIError(status_code=500, code=500, message="Failed to decode process listing output.")


def _parse_ps_output(output: str) -> List[ProcessEntry]:
    processes: List[ProcessEntry] = []

    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue

        match = re.match(r"^\s*(\d+)\s+(.*)$", line)
        if not match:
            continue

        pid = int(match.group(1))
        proc_string = match.group(2).strip()
        processes.append(ProcessEntry(processId=pid, processString=proc_string))

    return processes


@app.post(
    "/monitor/commands",
    response_model=List[ProcessEntry],
    responses={
        400: {"model": ErrorModel},
        500: {"model": ErrorModel},
    },
    summary="Retrieve a list of running processes",
    description="Returns the list of processes running on the server whose command string matches a given regular expression.",
)
def monitor_commands(request: MonitorRequest) -> List[ProcessEntry]:
    _validate_request_fields(request)
    regex = _safe_compile_regex(request.command_regex)
    output = _run_ps(request.filter_flags)
    processes = _parse_ps_output(output)
    return [proc for proc in processes if regex.search(proc.processString)]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)