import os
import asyncio
import tempfile
from pathlib import Path
from typing import Tuple, Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, validator, ValidationError

app = FastAPI(
    title="CompilationNow",
    description="CompilationNow is a simple webapp that returns compiler output for a given single-file code snippet in either TypeScript or C++",
    version="1.0.0",
)


MAX_FILENAME_LENGTH = 255
MAX_FILECONTENT_SIZE = 100 * 1024  # 100 KiB
FILENAME_REGEX = r"^[\w\-.]+$"


class CompileRequest(BaseModel):
    fileName: str = Field(..., example="main.ts")
    fileContent: str = Field(..., example="let x = 2 * 15;")

    @validator("fileName")
    def validate_filename(cls, v: str) -> str:
        if len(v) > MAX_FILENAME_LENGTH:
            raise ValueError("Filename too long")
        if "/" in v or "\\" in v:
            raise ValueError("Filename must not contain path separators")
        if not Path(v).name == v:
            raise ValueError("Filename must not contain directory components")
        import re

        if not re.match(FILENAME_REGEX, v):
            raise ValueError(
                "Filename contains invalid characters; only alphanumerics, underscore, hyphen and dot are allowed"
            )
        return v

    @validator("fileContent")
    def validate_filecontent(cls, v: str) -> str:
        if len(v.encode("utf-8")) > MAX_FILECONTENT_SIZE:
            raise ValueError("File content exceeds maximum allowed size")
        return v


class CompileResponse(BaseModel):
    hasError: bool = Field(..., example=True)
    compilerError: Optional[str] = Field(
        None, description="Error output from the compiler, null if compilation succeeded"
    )


async def _run_subprocess(
    cmd: list[str], cwd: str, timeout: int = 15
) -> Tuple[int, str, str]:
    """
    Execute a subprocess command asynchronously, capturing stdout and stderr.
    Returns a tuple of (returncode, stdout, stderr).
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout)
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        return proc.returncode, stdout, stderr
    except asyncio.TimeoutError:
        return 1, "", f"Compilation timed out after {timeout} seconds."
    except Exception as exc:
        return 1, "", f"Unexpected error: {str(exc)}"


async def compile_typescript(source_path: str, cwd: str) -> Tuple[bool, str]:
    """
    Compile a TypeScript file using `tsc --noEmit`.
    Returns (has_error, error_output)
    """
    cmd = ["tsc", "--noEmit", source_path]
    returncode, stdout, stderr = await _run_subprocess(cmd, cwd)
    if returncode == 0:
        return False, ""
    error_msg = stderr.strip() or stdout.strip()
    return True, error_msg


async def compile_cpp(source_path: str, cwd: str) -> Tuple[bool, str]:
    """
    Compile a C++ file using `g++ -std=c++17 -Wall -Wextra -c`.
    Returns (has_error, error_output)
    """
    cmd = ["g++", "-std=c++17", "-Wall", "-Wextra", "-c", source_path]
    returncode, stdout, stderr = await _run_subprocess(cmd, cwd)
    if returncode == 0:
        return False, ""
    error_msg = stderr.strip() or stdout.strip()
    return True, error_msg


@app.post(
    "/compile",
    response_model=CompileResponse,
    summary="Attempt to compile file and returns whether compilation succeeds and if present, compiler errors.",
)
async def compile_endpoint(request: CompileRequest):
    # Validate file extension
    _, ext = os.path.splitext(request.fileName.lower())
    if ext not in {".ts", ".cpp"}:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file extension. Use .ts for TypeScript or .cpp for C++.",
        )

    # Use a temporary directory as a sandbox
    with tempfile.TemporaryDirectory() as tmpdir:
        # Ensure the filename is safe (already validated) and join with tmpdir
        safe_path = os.path.join(tmpdir, request.fileName)

        # Write the source code to the temporary file
        try:
            with open(safe_path, "w", encoding="utf-8") as f:
                f.write(request.fileContent)
        except OSError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to write temporary source file: {e}",
            )

        # Choose the appropriate compilation routine
        if ext == ".ts":
            has_error, error_output = await compile_typescript(request.fileName, cwd=tmpdir)
        else:  # .cpp
            has_error, error_output = await compile_cpp(request.fileName, cwd=tmpdir)

        if not has_error:
            return CompileResponse(hasError=False, compilerError=None)
        else:
            error_output = error_output or "Compilation failed with unknown error."
            return CompileResponse(hasError=True, compilerError=error_output)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)