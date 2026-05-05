import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="CompilationNow", version="1.0.0")


class CompileRequest(BaseModel):
    fileName: str
    fileContent: str


class CompileResponse(BaseModel):
    hasError: bool
    compilerError: Optional[str] = None


def _run_subprocess(command: list[str], cwd: str, timeout: int = 10) -> subprocess.CompletedProcess:
    """
    Run a subprocess command with a timeout and capture stdout+stderr.
    """
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        # Return a CompletedProcess-like object indicating timeout
        return subprocess.CompletedProcess(
            args=exc.cmd,
            returncode=-1,
            stdout="",
            stderr=f"Process timed out after {timeout} seconds.",
        )


def _compile_typescript(source_path: str) -> Tuple[bool, Optional[str]]:
    """
    Compile (type‑check) a TypeScript file using `tsc --noEmit`.
    Returns (has_error, error_message).
    """
    result = _run_subprocess(
        ["tsc", source_path, "--noEmit", "--pretty", "false"],
        cwd=os.path.dirname(source_path),
    )
    if result.returncode == 0:
        return False, None
    error_msg = (result.stdout + "\n" + result.stderr).strip()
    return True, error_msg if error_msg else "TypeScript compilation failed."


def _compile_cpp(source_path: str) -> Tuple[bool, Optional[str]]:
    """
    Compile a C++ file using g++ (syntax check only, no output file).
    Returns (has_error, error_message).
    """
    result = _run_subprocess(
        [
            "g++",
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-pedantic",
            "-c",
            source_path,
            "-o",
            os.devnull,
        ],
        cwd=os.path.dirname(source_path),
    )
    if result.returncode == 0:
        return False, None
    error_msg = (result.stdout + "\n" + result.stderr).strip()
    return True, error_msg if error_msg else "C++ compilation failed."


def _sanitize_filename(filename: str) -> str:
    """
    Ensure the filename does not contain path traversal characters or separators.
    Returns a safe basename if valid, otherwise raises HTTPException.
    """
    # Reject absolute paths
    if Path(filename).is_absolute():
        raise HTTPException(
            status_code=400,
            detail="Filename must be a relative name without path components.",
        )
    # Reject any path separators or parent directory references
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(
            status_code=400,
            detail="Filename must not contain path traversal characters.",
        )
    # Use only the final component to avoid hidden subdirectories
    safe_name = Path(filename).name
    if safe_name != filename:
        raise HTTPException(
            status_code=400,
            detail="Filename must not contain directory components.",
        )
    return safe_name


@app.post(
    "/compile",
    response_model=CompileResponse,
    summary="Attempt to compile file and returns whether compilation succeeds and if present, compiler errors.",
)
def compile_code(request: CompileRequest) -> CompileResponse:
    # Validate and sanitize filename
    safe_filename = _sanitize_filename(request.fileName)

    # Determine language from file extension
    _, ext = os.path.splitext(safe_filename.lower())
    if ext not in {".ts", ".cpp"}:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file extension. Only .ts and .cpp are allowed.",
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        source_path = os.path.join(tmpdir, safe_filename)

        # Write the source code to a temporary file
        try:
            with open(source_path, "w", encoding="utf-8") as f:
                f.write(request.fileContent)
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to write source file: {exc}",
            ) from exc

        if ext == ".ts":
            has_error, error_msg = _compile_typescript(source_path)
        else:  # .cpp
            has_error, error_msg = _compile_cpp(source_path)

        return CompileResponse(hasError=has_error, compilerError=error_msg)


if __name__ == "__main__":
    import uvicorn

    # Allow the port to be overridden via the PORT environment variable (useful for tests)
    port_str = os.getenv("PORT", "5000")
    try:
        port = int(port_str)
    except ValueError:
        raise RuntimeError(f"Invalid PORT value: {port_str}")

    uvicorn.run(app, host="0.0.0.0", port=port)