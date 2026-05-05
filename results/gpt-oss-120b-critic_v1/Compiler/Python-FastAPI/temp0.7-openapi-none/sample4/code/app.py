import os
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Optional, Tuple

from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field, validator

# Configuration constants
MAX_CONTENT_SIZE = 100 * 1024          # 100 KiB max source size
MAX_OUTPUT_SIZE = 20 * 1024           # 20 KiB max captured compiler output
MAX_CONCURRENT_COMPILATIONS = 5       # Limit concurrent compile jobs

# Concurrency guard
compile_semaphore = threading.Semaphore(MAX_CONCURRENT_COMPILATIONS)

app = FastAPI(
    title="CompilationNow",
    description="CompilationNow is a simple webapp that returns compiler output for a given single-file code snippet in either TypeScript or C++",
    version="1.0.0",
)


class CompileRequest(BaseModel):
    fileName: str = Field(..., example="main.ts")
    fileContent: str = Field(..., example='let x = 2 * 15;')

    @validator("fileName")
    def filename_must_be_safe(cls, v: str) -> str:
        # Reject absolute paths and path traversal
        if Path(v).is_absolute():
            raise ValueError("Absolute paths are not allowed.")
        if ".." in v.split(os.path.sep):
            raise ValueError("Path traversal components are not allowed.")
        # Only allow a simple filename (no directory separators)
        if Path(v).name != v:
            raise ValueError("Only a plain filename without directories is allowed.")
        return v

    @validator("fileContent")
    def content_size_limit(cls, v: str) -> str:
        if len(v.encode("utf-8")) > MAX_CONTENT_SIZE:
            raise ValueError(f"Source size exceeds limit of {MAX_CONTENT_SIZE // 1024} KiB.")
        return v


class CompileResponse(BaseModel):
    hasError: bool = Field(..., example=True)
    compilerError: Optional[str] = Field(
        None,
        example="background.ts:1:9 - error TS2304: Cannot find name 'y'.",
    )


def _run_subprocess(cmd: list[str], cwd: Path) -> Tuple[int, str]:
    """
    Execute a subprocess command safely.
    Returns a tuple of (returncode, truncated_output).
    Handles missing executables and timeouts.
    """
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=15,
        )
        combined = (result.stdout + "\n" + result.stderr).strip()
    except subprocess.TimeoutExpired:
        return 1, "Compilation timed out."
    except FileNotFoundError:
        # The requested compiler binary does not exist
        return 1, f"Required compiler not found: {cmd[0]}"
    except Exception as exc:  # Catch any unexpected errors
        return 1, f"Internal compilation error: {str(exc)}"

    # Truncate output to avoid excessive memory usage
    if len(combined) > MAX_OUTPUT_SIZE:
        combined = combined[:MAX_OUTPUT_SIZE] + "\n[output truncated]"
    return result.returncode, combined


def _compile_cpp(source_path: Path) -> Tuple[bool, Optional[str]]:
    """
    Compile a C++ source file using g++.
    Returns (has_error, error_message_or_None).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_dir_path = Path(tmpdir)
        output_exe = tmp_dir_path / "a.out"
        cmd = [
            "g++",
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-xc++",
            "-o",
            str(output_exe),
            str(source_path),
        ]
        rc, out = _run_subprocess(cmd, cwd=tmp_dir_path)
        if rc != 0:
            return True, out
        return False, None


def _compile_typescript(source_path: Path) -> Tuple[bool, Optional[str]]:
    """
    Type‑check a TypeScript file using the global `tsc` compiler.
    Returns (has_error, error_message_or_None).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_dir_path = Path(tmpdir)
        cmd = [
            "tsc",
            str(source_path),
            "--noEmit",
            "--pretty",
            "false",
        ]
        rc, out = _run_subprocess(cmd, cwd=tmp_dir_path)
        if rc != 0:
            return True, out
        return False, None


@app.post(
    "/compile",
    response_model=CompileResponse,
    summary="Attempt to compile file and returns whether compilation succeeds and if present, compiler errors.",
    status_code=status.HTTP_200_OK,
)
def compile_endpoint(request: Request, payload: CompileRequest) -> CompileResponse:
    # Enforce concurrency limits
    if not compile_semaphore.acquire(blocking=False):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Server is busy processing other compilations. Please try again later.",
        )
    try:
        ext = Path(payload.fileName).suffix.lower()
        if ext not in {".cpp", ".cc", ".cxx", ".c", ".ts"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unsupported file extension. Use .cpp/.cc/.cxx/.c for C++ or .ts for TypeScript.",
            )

        # Write source to a safe temporary location
        with tempfile.TemporaryDirectory() as workdir:
            work_path = Path(workdir)
            source_path = work_path / payload.fileName  # filename already validated
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text(payload.fileContent, encoding="utf-8")

            if ext in {".cpp", ".cc", ".cxx", ".c"}:
                has_error, err_msg = _compile_cpp(source_path)
            else:  # .ts
                has_error, err_msg = _compile_typescript(source_path)

        return CompileResponse(hasError=has_error, compilerError=err_msg)
    finally:
        compile_semaphore.release()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)