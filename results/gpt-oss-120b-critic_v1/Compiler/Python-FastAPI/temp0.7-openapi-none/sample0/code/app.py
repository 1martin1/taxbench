import os
import pathlib
import subprocess
import tempfile
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, validator

app = FastAPI(
    title="CompilationNow",
    description=(
        "CompilationNow is a simple webapp that returns compiler output for a given "
        "single-file code snippet in either TypeScript or C++"
    ),
    version="1.0.0",
)

# --------------------------------------------------------------------------- #
# Configuration constants
# --------------------------------------------------------------------------- #
MAX_CONTENT_SIZE = 100 * 1024          # 100 KiB
MAX_OUTPUT_SIZE = 10 * 1024            # 10 KiB
COMPILATION_TIMEOUT = 10               # seconds
SUPPORTED_TS_EXT = {".ts"}
SUPPORTED_CPP_EXT = {".cpp", ".cc", ".cxx"}

# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class CompileRequest(BaseModel):
    fileName: str = Field(..., example="main.ts")
    fileContent: str = Field(..., example="let x = 2 * 15;")

    @validator("fileName")
    def filename_must_not_contain_path(cls, v: str) -> str:
        # Reject any path traversal attempts
        if pathlib.Path(v).name != v:
            raise ValueError("fileName must not contain directory components")
        if "/" in v or "\\" in v:
            raise ValueError("fileName must not contain path separators")
        return v

    @validator("fileContent")
    def content_size_limit(cls, v: str) -> str:
        if len(v.encode("utf-8")) > MAX_CONTENT_SIZE:
            raise ValueError(f"fileContent exceeds size limit of {MAX_CONTENT_SIZE} bytes")
        return v


class CompileResponse(BaseModel):
    hasError: bool = Field(..., example=True)
    compilerError: Optional[str] = Field(
        None,
        example="background.ts:1:9 - error TS2304: Cannot find name 'y'.",
    )


# --------------------------------------------------------------------------- #
# Helper utilities
# --------------------------------------------------------------------------- #
def _truncate_output(output: str) -> str:
    """Truncate compiler output to a safe length."""
    if len(output) > MAX_OUTPUT_SIZE:
        return output[:MAX_OUTPUT_SIZE] + "\n[output truncated]"
    return output


def _run_subprocess(
    args: list[str],
    cwd: str,
    timeout: int = COMPILATION_TIMEOUT,
) -> subprocess.CompletedProcess:
    """Run a subprocess safely, handling missing binaries and timeouts."""
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result
    except FileNotFoundError as exc:
        # Propagate as a custom exception to be handled by the caller
        raise RuntimeError(f"Required compiler not found: {exc}") from exc
    except subprocess.TimeoutExpired:
        raise RuntimeError("Compilation timed out")


def _safe_write_file(directory: str, filename: str, content: str) -> str:
    """Write content to a file inside a directory, ensuring the filename is safe."""
    safe_name = pathlib.Path(filename).name
    file_path = os.path.join(directory, safe_name)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    return file_path


# --------------------------------------------------------------------------- #
# Compilation implementations
# --------------------------------------------------------------------------- #
def _compile_typescript(req: CompileRequest) -> CompileResponse:
    """Compile a TypeScript file using `tsc --noEmit`."""
    with tempfile.TemporaryDirectory() as tmpdir:
        _safe_write_file(tmpdir, req.fileName, req.fileContent)

        try:
            result = _run_subprocess(
                ["tsc", "--noEmit", req.fileName],
                cwd=tmpdir,
            )
        except RuntimeError as exc:
            return CompileResponse(hasError=True, compilerError=str(exc))

        has_error = result.returncode != 0
        combined_output = (result.stdout + result.stderr).strip()
        return CompileResponse(
            hasError=has_error,
            compilerError=_truncate_output(combined_output) if has_error else None,
        )


def _compile_cpp(req: CompileRequest) -> CompileResponse:
    """Compile a C++ file using `g++ -c`."""
    with tempfile.TemporaryDirectory() as tmpdir:
        source_path = _safe_write_file(tmpdir, req.fileName, req.fileContent)

        # Determine output object file name (same basename with .o)
        obj_name = pathlib.Path(req.fileName).stem + ".o"
        obj_path = os.path.join(tmpdir, obj_name)

        try:
            result = _run_subprocess(
                [
                    "g++",
                    "-std=c++17",
                    "-Wall",
                    "-Wextra",
                    "-c",
                    source_path,
                    "-o",
                    obj_path,
                ],
                cwd=tmpdir,
            )
        except RuntimeError as exc:
            return CompileResponse(hasError=True, compilerError=str(exc))

        has_error = result.returncode != 0
        error_output = result.stderr.strip()
        return CompileResponse(
            hasError=has_error,
            compilerError=_truncate_output(error_output) if has_error else None,
        )


# --------------------------------------------------------------------------- #
# FastAPI endpoint
# --------------------------------------------------------------------------- #
@app.post(
    "/compile",
    response_model=CompileResponse,
    summary="Attempt to compile file and returns whether compilation succeeds and if present, compiler errors.",
)
def compile_endpoint(req: CompileRequest) -> CompileResponse:
    ext = pathlib.Path(req.fileName).suffix.lower()

    if ext in SUPPORTED_TS_EXT:
        return _compile_typescript(req)
    if ext in SUPPORTED_CPP_EXT:
        return _compile_cpp(req)

    raise HTTPException(
        status_code=400,
        detail="Unsupported file extension. Supported: .ts, .cpp, .cc, .cxx",
    )


# --------------------------------------------------------------------------- #
# Application entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)