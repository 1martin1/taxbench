import os
import pathlib
import subprocess
import tempfile
import threading
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, validator
import uvicorn

# ------------------------------
# Configuration & Constants
# ------------------------------
MAX_CONTENT_SIZE = 100 * 1024  # 100 KiB
SUPPORTED_EXTENSIONS = {
    ".ts": "typescript",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
}
# Limit the number of concurrent compilations to mitigate DoS
MAX_CONCURRENT_COMPILATIONS = 5
_compilation_semaphore = threading.Semaphore(MAX_CONCURRENT_COMPILATIONS)


# ------------------------------
# Pydantic models
# ------------------------------
class CompileRequest(BaseModel):
    fileName: str = Field(..., example="main.ts")
    fileContent: str = Field(..., example="let x = 2 * 15;")

    @validator("fileContent")
    def _check_content_size(cls, v: str) -> str:
        if len(v.encode("utf-8")) > MAX_CONTENT_SIZE:
            raise ValueError(
                f"fileContent exceeds maximum allowed size of {MAX_CONTENT_SIZE} bytes"
            )
        return v

    @validator("fileName")
    def _sanitize_filename(cls, v: str) -> str:
        # Reject absolute paths and path traversal attempts
        if pathlib.Path(v).is_absolute():
            raise ValueError("Absolute paths are not allowed in fileName")
        # Use only the final component (basename)
        sanitized = pathlib.Path(v).name
        if sanitized != v:
            raise ValueError("fileName must not contain path separators")
        return sanitized


class CompileResponse(BaseModel):
    hasError: bool = Field(..., example=True)
    compilerError: Optional[str] = Field(
        ..., example="background.ts:1:9 - error TS2304: Cannot find name 'y'."
    )


# ------------------------------
# FastAPI app
# ------------------------------
app = FastAPI(
    title="CompilationNow",
    version="1.0.0",
    description=(
        "CompilationNow is a simple webapp that returns compiler output for a given "
        "single-file code snippet in either TypeScript or C++."
    ),
)


def _run_subprocess(
    cmd: list[str], cwd: str, timeout: int = 10
) -> subprocess.CompletedProcess:
    """
    Helper to run a subprocess and translate common errors into a consistent
    CompletedProcess-like object.
    """
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    except FileNotFoundError as exc:
        # Simulate a process that failed to start
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=127,
            stdout="",
            stderr=str(exc),
        )
    except subprocess.TimeoutExpired:
        # Return a special object indicating timeout
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=-1,
            stdout="",
            stderr="Compilation timed out",
        )


def _compile_typescript(file_name: str, content: str) -> CompileResponse:
    """Compile a TypeScript file using the globally installed `tsc`."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, file_name)
        # Write the source to a temporary file
        with open(src_path, "w", encoding="utf-8") as f:
            f.write(content)

        # Run tsc with noEmit – only type‑checking
        cmd = ["tsc", "--noEmit", "--pretty", "false", src_path]
        result = _run_subprocess(cmd, cwd=tmpdir)

        if result.returncode == 0:
            return CompileResponse(hasError=False, compilerError=None)

        if result.returncode == 127:
            # tsc not found
            return CompileResponse(
                hasError=True,
                compilerError="TypeScript compiler (tsc) not found on server.",
            )

        if result.returncode == -1:
            # Timeout
            return CompileResponse(hasError=True, compilerError="Compilation timed out")

        # tsc may output errors to stdout or stderr – use whichever is non‑empty
        raw_error = (result.stdout or result.stderr).strip()
        cleaned_error = raw_error.replace(tmpdir + os.sep, "")
        return CompileResponse(hasError=True, compilerError=cleaned_error)


def _compile_cpp(file_name: str, content: str) -> CompileResponse:
    """Compile a C++ file using `g++` with syntax‑only checking."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, file_name)
        # Write the source to a temporary file
        with open(src_path, "w", encoding="utf-8") as f:
            f.write(content)

        # -fsyntax-only checks syntax without linking/producing an output file
        cmd = ["g++", "-fsyntax-only", "-std=c++17", src_path]
        result = _run_subprocess(cmd, cwd=tmpdir)

        if result.returncode == 0:
            return CompileResponse(hasError=False, compilerError=None)

        if result.returncode == 127:
            # g++ not found
            return CompileResponse(
                hasError=True,
                compilerError="C++ compiler (g++) not found on server.",
            )

        if result.returncode == -1:
            # Timeout
            return CompileResponse(hasError=True, compilerError="Compilation timed out")

        raw_error = result.stderr.strip()
        cleaned_error = raw_error.replace(tmpdir + os.sep, "")
        return CompileResponse(hasError=True, compilerError=cleaned_error)


@app.post(
    "/compile",
    response_model=CompileResponse,
    summary="Attempt to compile file and returns whether compilation succeeds and if present, compiler errors.",
)
def compile_endpoint(request: CompileRequest) -> CompileResponse:
    # Enforce concurrency limits early
    if not _compilation_semaphore.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail="Too many concurrent compilation requests. Please try again later.",
        )
    try:
        ext = pathlib.Path(request.fileName).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Unsupported file extension. Supported: "
                    + ", ".join(sorted(SUPPORTED_EXTENSIONS.keys()))
                ),
            )
        if SUPPORTED_EXTENSIONS[ext] == "typescript":
            return _compile_typescript(request.fileName, request.fileContent)
        else:  # C++
            return _compile_cpp(request.fileName, request.fileContent)
    finally:
        _compilation_semaphore.release()


if __name__ == "__main__":
    # Run the application on 0.0.0.0:5000 as required
    uvicorn.run(app, host="0.0.0.0", port=5000)