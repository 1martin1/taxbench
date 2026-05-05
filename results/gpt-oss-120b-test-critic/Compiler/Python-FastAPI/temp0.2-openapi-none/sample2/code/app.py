import os
import pathlib
import subprocess
import tempfile
from typing import Optional, Tuple

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

# Maximum allowed source size (in bytes) to prevent resource exhaustion.
MAX_SOURCE_SIZE = 100 * 1024  # 100 KiB


class CompileRequest(BaseModel):
    fileName: str = Field(..., example="main.ts")
    fileContent: str = Field(..., example="let x = 2 * 15;")

    @validator("fileName")
    def filename_must_be_safe(cls, v: str) -> str:
        """
        Ensure the filename does not contain path traversal components.
        Only a simple filename (no directories) is allowed.
        """
        # Reject absolute paths
        if pathlib.PurePath(v).is_absolute():
            raise ValueError("Absolute paths are not allowed.")
        # Reject any parent directory references or path separators
        if ".." in pathlib.PurePath(v).parts or any(sep in v for sep in (os.sep, "/")):
            raise ValueError("Path traversal components are not allowed.")
        # Ensure a non‑empty filename
        if not v.strip():
            raise ValueError("Filename cannot be empty.")
        return v

    @validator("fileContent")
    def content_size_limit(cls, v: str) -> str:
        """Enforce a maximum size on the source code."""
        if len(v.encode("utf-8")) > MAX_SOURCE_SIZE:
            raise ValueError(f"Source size exceeds {MAX_SOURCE_SIZE // 1024} KiB limit.")
        return v


class CompileResponse(BaseModel):
    hasError: bool = Field(..., example=True)
    compilerError: Optional[str] = Field(
        None, example="background.ts:1:9 - error TS2304: Cannot find name 'y'."
    )


def _run_typescript(source_path: str) -> Tuple[bool, Optional[str]]:
    """Compile a TypeScript file using `tsc` with no emit."""
    try:
        result = subprocess.run(
            ["tsc", source_path, "--noEmit", "--pretty", "false"],
            capture_output=True,
            text=True,
            timeout=10,
            env={"PATH": os.environ.get("PATH", "")},
        )
    except Exception as exc:
        return True, f"Failed to run tsc: {exc}"

    if result.returncode == 0:
        return False, None
    # tsc writes diagnostics to stderr
    return True, result.stderr.strip() or None


def _run_cpp(source_path: str) -> Tuple[bool, Optional[str]]:
    """Compile a C++ file using g++."""
    try:
        result = subprocess.run(
            [
                "g++",
                source_path,
                "-std=c++17",
                "-Wall",
                "-Wextra",
                "-o",
                os.devnull,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            env={"PATH": os.environ.get("PATH", "")},
        )
    except Exception as exc:
        return True, f"Failed to run g++: {exc}"

    if result.returncode == 0:
        return False, None
    return True, result.stderr.strip() or None


@app.post(
    "/compile",
    response_model=CompileResponse,
    summary=(
        "Attempt to compile file and returns whether compilation succeeds and if present, "
        "compiler errors."
    ),
)
def compile_code(request: CompileRequest) -> CompileResponse:
    ext = pathlib.Path(request.fileName).suffix.lower()
    supported_ts = {".ts", ".tsx"}
    supported_cpp = {".cpp", ".cc", ".cxx", ".c"}

    if ext not in supported_ts.union(supported_cpp):
        raise HTTPException(status_code=400, detail="Unsupported file extension")

    # Use a temporary directory to isolate the compilation.
    with tempfile.TemporaryDirectory() as tmpdir:
        # Safely construct the target path using only the sanitized filename.
        target_path = os.path.join(tmpdir, request.fileName)

        # Write the source file.
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(request.fileContent)

        # Choose the appropriate compiler.
        if ext in supported_ts:
            has_error, err = _run_typescript(target_path)
        else:
            has_error, err = _run_cpp(target_path)

        return CompileResponse(hasError=has_error, compilerError=err)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)