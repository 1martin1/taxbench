import os
import shutil
import subprocess
import tempfile
import asyncio
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel, validator

# Configuration constants
MAX_FILE_SIZE = 100 * 1024          # 100 KiB per request
MAX_CONCURRENT_COMPILATIONS = 5     # limit concurrent compiler processes
COMPILER_TIMEOUT = 10               # seconds

# Global concurrency semaphore
_compilation_semaphore = asyncio.Semaphore(MAX_CONCURRENT_COMPILATIONS)

# Verify that required external tools are available
_TSC_PATH = shutil.which("tsc")
_GPP_PATH = shutil.which("g++")

if not _TSC_PATH:
    # Logically we could start without tsc, but requests for .ts will be rejected gracefully
    pass
if not _GPP_PATH:
    # Same for g++
    pass

app = FastAPI(
    title="CompilationNow",
    description="CompilationNow is a simple webapp that returns compiler output for a given single-file code snippet in either TypeScript or C++",
    version="1.0.0",
)


class CompileRequest(BaseModel):
    fileName: str
    fileContent: str

    @validator("fileContent")
    def check_size(cls, v: str) -> str:
        if len(v.encode("utf-8")) > MAX_FILE_SIZE:
            raise ValueError(f"fileContent exceeds maximum allowed size of {MAX_FILE_SIZE} bytes")
        return v


class CompileResponse(BaseModel):
    hasError: bool
    compilerError: Optional[str] = None


def _sanitize_error_output(raw_output: str, temp_dir: str) -> str:
    """
    Remove absolute temporary paths and limit the length of the output.
    """
    placeholder = "<temp>"
    sanitized = raw_output.replace(temp_dir, placeholder)
    # Truncate to a reasonable length to avoid leaking too much info
    max_len = 2000
    if len(sanitized) > max_len:
        sanitized = sanitized[:max_len] + "...[truncated]"
    return sanitized.strip()


def _run_subprocess(command: list[str], cwd: str) -> subprocess.CompletedProcess:
    """
    Execute a subprocess command synchronously. This function is intended to be called
    inside a thread (via asyncio.to_thread) so it can block without affecting the event loop.
    """
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=COMPILER_TIMEOUT,
        )
        return result
    except FileNotFoundError as exc:
        # Propagate a clear error indicating missing compiler
        raise RuntimeError(f"Required compiler not found: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Compilation timed out") from exc


def _compile_typescript(source_path: str, temp_dir: str) -> tuple[bool, Optional[str]]:
    """
    Compile (type‑check) a TypeScript file using the globally installed `tsc`.
    `--noEmit` ensures no JavaScript output is produced.
    Returns (has_error, error_message).
    """
    if not _TSC_PATH:
        raise RuntimeError("TypeScript compiler (tsc) is not installed on the server.")
    result = _run_subprocess([_TSC_PATH, source_path, "--noEmit"], cwd=temp_dir)
    has_error = result.returncode != 0
    if has_error:
        # tsc may write diagnostics to stdout or stderr; combine both.
        raw_error = (result.stdout + "\n" + result.stderr).strip()
        return True, _sanitize_error_output(raw_error, temp_dir)
    return False, None


def _compile_cpp(source_path: str, temp_dir: str) -> tuple[bool, Optional[str]]:
    """
    Compile a C++ file using g++. The output is directed to /dev/null,
    we only care about the return code and any diagnostics.
    Returns (has_error, error_message).
    """
    if not _GPP_PATH:
        raise RuntimeError("C++ compiler (g++) is not installed on the server.")
    result = _run_subprocess(
        [_GPP_PATH, "-std=c++17", "-Wall", "-Wextra", "-c", source_path, "-o", os.devnull],
        cwd=temp_dir,
    )
    has_error = result.returncode != 0
    if has_error:
        raw_error = (result.stdout + "\n" + result.stderr).strip()
        return True, _sanitize_error_output(raw_error, temp_dir)
    return False, None


async def _perform_compilation(file_name: str, file_content: str) -> CompileResponse:
    """
    Handles the whole compilation workflow inside a temporary directory.
    """
    _, ext = os.path.splitext(file_name)
    ext = ext.lower()

    if ext not in {".ts", ".cpp", ".cc", ".cxx"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file extension. Only .ts (TypeScript) and .cpp/.cc/.cxx (C++) are allowed.",
        )

    # Create temporary workspace
    with tempfile.TemporaryDirectory() as tmpdir:
        source_path = os.path.join(tmpdir, f"source{ext}")
        # Write source safely
        async with asyncio.to_thread(open, source_path, "w", encoding="utf-8") as f:
            # Using to_thread for file write is unnecessary; open is quick, but keep consistency
            pass
        # Simpler synchronous write (fast, small files)
        with open(source_path, "w", encoding="utf-8") as f:
            f.write(file_content)

        # Choose compiler
        if ext == ".ts":
            has_error, error_msg = _compile_typescript(source_path, tmpdir)
        else:
            has_error, error_msg = _compile_cpp(source_path, tmpdir)

        return CompileResponse(hasError=has_error, compilerError=error_msg)


@app.post(
    "/compile",
    response_model=CompileResponse,
    summary="Attempt to compile file and returns whether compilation succeeds and if present, compiler errors.",
)
async def compile_endpoint(request: CompileRequest) -> CompileResponse:
    # Enforce concurrency limit
    async with _compilation_semaphore:
        try:
            response = await _perform_compilation(request.fileName, request.fileContent)
            return response
        except RuntimeError as exc:
            # Map internal errors to a clean HTTP response without leaking implementation details
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(exc),
            )
        except Exception as exc:
            # Unexpected errors – log if needed and return generic message
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An unexpected error occurred during compilation.",
            ) from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)