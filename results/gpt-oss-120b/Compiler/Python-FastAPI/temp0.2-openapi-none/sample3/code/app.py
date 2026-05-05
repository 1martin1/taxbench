import os
import subprocess
import tempfile
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(
    title="CompilationNow",
    description="CompilationNow is a simple webapp that returns compiler output for a given single-file code snippet in either TypeScript or C++",
    version="1.0.0",
)


class CompileRequest(BaseModel):
    fileName: str
    fileContent: str


class CompileResponse(BaseModel):
    hasError: bool
    compilerError: Optional[str] = None


def _compile_typescript(file_path: str) -> (bool, str):
    """
    Compile a TypeScript file using `tsc --noEmit`.
    Returns (has_error, error_message).
    """
    # `--noEmit` checks types without producing output files.
    result = subprocess.run(
        ["tsc", "--noEmit", file_path],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return False, None
    # tsc prints errors to stderr
    return True, result.stderr.strip() or None


def _compile_cpp(file_path: str) -> (bool, str):
    """
    Compile a C++ file using `g++`.
    Returns (has_error, error_message).
    """
    # Compile to a temporary binary; we discard it afterwards.
    out_path = file_path + ".out"
    result = subprocess.run(
        ["g++", "-std=c++17", "-Wall", "-Wextra", file_path, "-o", out_path],
        capture_output=True,
        text=True,
    )
    # Clean up the binary if it was created.
    if os.path.exists(out_path):
        try:
            os.remove(out_path)
        except OSError:
            pass

    if result.returncode == 0:
        return False, None
    return True, result.stderr.strip() or None


@app.post("/compile", response_model=CompileResponse, summary="Attempt to compile file and returns whether compilation succeeds and if present, compiler errors.")
def compile_endpoint(payload: CompileRequest):
    # Determine language from file extension
    _, ext = os.path.splitext(payload.fileName.lower())
    if ext not in {".ts", ".cpp"}:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Only .ts and .cpp are allowed.",
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = os.path.join(tmpdir, payload.fileName)
        # Write the source code to the temporary file
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(payload.fileContent)

        if ext == ".ts":
            has_error, err_msg = _compile_typescript(file_path)
        else:  # .cpp
            has_error, err_msg = _compile_cpp(file_path)

        return CompileResponse(hasError=has_error, compilerError=err_msg)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)