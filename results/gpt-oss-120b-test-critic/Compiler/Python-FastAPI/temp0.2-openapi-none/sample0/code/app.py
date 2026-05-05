import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(
    title="CompilationNow",
    description="CompilationNow is a simple webapp that returns compiler output for a given single-file code snippet in either TypeScript or C++",
    version="1.0.0",
)


class CompileRequest(BaseModel):
    fileName: str = Field(..., example="main.ts")
    fileContent: str = Field(..., example="let x = 2 * 15;")


class CompileResponse(BaseModel):
    hasError: bool = Field(..., example=True)
    compilerError: Optional[str] = Field(
        None,
        example="background.ts:1:9 - error TS2304: Cannot find name 'y'.",
    )


def _run_typescript(file_path: str) -> subprocess.CompletedProcess:
    """
    Run the TypeScript compiler (tsc) on the given file.
    --noEmit prevents output files, --pretty false gives plain error messages.
    """
    cmd = [
        "tsc",
        file_path,
        "--noEmit",
        "--pretty",
        "false",
    ]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _run_cpp(file_path: str) -> subprocess.CompletedProcess:
    """
    Compile the C++ source file using g++.
    -c compiles only (no linking), -Wall enables warnings.
    """
    cmd = [
        "g++",
        "-c",
        file_path,
        "-o",
        "/dev/null",
        "-Wall",
    ]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _sanitize_error_output(error_output: str, temp_path: str, original_name: str) -> str:
    """
    Remove internal absolute paths from compiler output.
    Replace the temporary file path with the user‑provided file name.
    """
    # Replace the exact temporary path
    sanitized = error_output.replace(temp_path, original_name)

    # Additionally replace any other absolute paths that contain the temp file name
    temp_basename = os.path.basename(temp_path)
    sanitized = sanitized.replace(temp_basename, original_name)

    # As a safety net, strip any leading directory components from paths that look like /.../
    # This simple regex replaces patterns like /tmp/abcd/main.cpp with main.cpp
    import re

    sanitized = re.sub(r"(/[^\s]+)+/([^/]+\.(ts|cpp))", r"\2", sanitized)
    return sanitized


def _validate_filename(file_name: str) -> None:
    """
    Ensure the supplied file name does not contain path traversal characters.
    """
    # Reject absolute paths
    if Path(file_name).is_absolute():
        raise HTTPException(
            status_code=400,
            detail="fileName must be a relative filename without path components.",
        )
    # Reject directory separators or parent‑directory references
    if ".." in file_name or "/" in file_name or "\\" in file_name:
        raise HTTPException(
            status_code=400,
            detail="fileName must not contain path traversal characters.",
        )
    # Ensure the name has a stem (i.e., not empty)
    if Path(file_name).name != file_name or not Path(file_name).stem:
        raise HTTPException(
            status_code=400,
            detail="Invalid fileName.",
        )


@app.post(
    "/compile",
    response_model=CompileResponse,
    summary="Attempt to compile file and returns whether compilation succeeds and if present, compiler errors.",
)
def compile_code(request: CompileRequest) -> CompileResponse:
    # Validate filename to prevent path traversal attacks
    _validate_filename(request.fileName)

    _, ext = os.path.splitext(request.fileName.lower())
    if ext not in {".ts", ".cpp"}:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file extension. Only .ts and .cpp are allowed.",
        )

    # Create a temporary file with the appropriate suffix
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=ext, delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(request.fileContent)
        tmp_path = tmp.name

    try:
        if ext == ".ts":
            result = _run_typescript(tmp_path)
        else:  # .cpp
            result = _run_cpp(tmp_path)

        has_error = result.returncode != 0
        # tsc prints errors to stdout, g++ to stderr – capture both
        raw_error = result.stdout.strip() if result.stdout else result.stderr.strip()
        compiler_error = None
        if has_error and raw_error:
            compiler_error = _sanitize_error_output(
                raw_error, tmp_path, request.fileName
            )
        return CompileResponse(hasError=has_error, compilerError=compiler_error)
    finally:
        # Clean up the temporary file
        try:
            os.remove(tmp_path)
        except OSError:
            pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)