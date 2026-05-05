import os
import subprocess
import tempfile
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
        None, example="background.ts:1:9 - error TS2304: Cannot find name 'y'."
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


@app.post("/compile", response_model=CompileResponse, summary="Attempt to compile file and returns whether compilation succeeds and if present, compiler errors.")
def compile_code(request: CompileRequest) -> CompileResponse:
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
        error_output = result.stdout.strip() if result.stdout else result.stderr.strip()
        compiler_error = error_output if has_error and error_output else None

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