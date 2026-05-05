import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, validator

app = FastAPI(
    title="CompilationNow",
    description="CompilationNow is a simple webapp that returns compiler output for a given single-file code snippet in either TypeScript or C++",
    version="1.0.0",
)

# Maximum allowed size for the source code (100 KiB)
MAX_CONTENT_SIZE = 100 * 1024  # bytes
# Maximum time a compilation process may run (seconds)
COMPILATION_TIMEOUT = 10


class CompileRequest(BaseModel):
    fileName: str
    fileContent: str

    @validator("fileName")
    def filename_must_be_simple(cls, v: str) -> str:
        # Disallow path separators to avoid any confusion about the source of the file
        if Path(v).name != v:
            raise ValueError("fileName must not contain path separators")
        return v

    @validator("fileContent")
    def content_size_limit(cls, v: str) -> str:
        if len(v.encode("utf-8")) > MAX_CONTENT_SIZE:
            raise ValueError(f"fileContent exceeds maximum allowed size of {MAX_CONTENT_SIZE} bytes")
        return v


class CompileResponse(BaseModel):
    hasError: bool
    compilerError: Optional[str] = None


def _run_typescript(file_path: Path) -> Tuple[bool, Optional[str]]:
    """Compile TypeScript file using `tsc --noEmit`."""
    try:
        result = subprocess.run(
            ["tsc", "--noEmit", str(file_path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=COMPILATION_TIMEOUT,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="TypeScript compiler (tsc) not found.")
    except subprocess.TimeoutExpired:
        return True, "Compilation timed out."
    has_error = result.returncode != 0
    error_msg = result.stderr.strip() if has_error else None
    return has_error, error_msg


def _run_cpp(file_path: Path) -> Tuple[bool, Optional[str]]:
    """Compile C++ file using `g++ -c`."""
    try:
        result = subprocess.run(
            [
                "g++",
                "-std=c++17",
                "-Wall",
                "-Wextra",
                "-c",
                str(file_path),
                "-o",
                os.devnull,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=COMPILATION_TIMEOUT,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="C++ compiler (g++) not found.")
    except subprocess.TimeoutExpired:
        return True, "Compilation timed out."
    has_error = result.returncode != 0
    error_msg = result.stderr.strip() if has_error else None
    return has_error, error_msg


@app.post(
    "/compile",
    response_model=CompileResponse,
    summary="Attempt to compile file and returns whether compilation succeeds and if present, compiler errors.",
)
def compile_code(request: CompileRequest):
    # Determine language by file extension
    _, ext = os.path.splitext(request.fileName.lower())
    if ext not in {".ts", ".tsx", ".cpp", ".cc", ".cxx", ".c"}:
        raise HTTPException(status_code=400, detail="Unsupported file extension.")

    # Create a temporary file with the appropriate suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext, mode="w", encoding="utf-8") as tmp:
        tmp.write(request.fileContent)
        tmp_path = Path(tmp.name)

    try:
        if ext in {".ts", ".tsx"}:
            has_error, error_msg = _run_typescript(tmp_path)
        else:
            has_error, error_msg = _run_cpp(tmp_path)

        return CompileResponse(hasError=has_error, compilerError=error_msg)
    finally:
        # Clean up the temporary file
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)