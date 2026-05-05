import os
import subprocess
import tempfile
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="CompilationNow", version="1.0.0")


class CompileRequest(BaseModel):
    fileName: str
    fileContent: str


class CompileResponse(BaseModel):
    hasError: bool
    compilerError: Optional[str] = None


def _run_subprocess(command: list[str], cwd: str) -> subprocess.CompletedProcess:
    """
    Helper to run a subprocess command and capture stdout+stderr.
    """
    return subprocess.run(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _compile_typescript(source_path: str) -> (bool, Optional[str]):
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
    # tsc prints errors to stdout when using --pretty false; combine both streams
    error_msg = (result.stdout + "\n" + result.stderr).strip()
    return True, error_msg if error_msg else "TypeScript compilation failed."


def _compile_cpp(source_path: str) -> (bool, Optional[str]):
    """
    Compile a C++ file using g++ (only syntax checking, no output file).
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


@app.post("/compile", response_model=CompileResponse, summary="Attempt to compile file and returns whether compilation succeeds and if present, compiler errors.")
def compile_code(request: CompileRequest) -> CompileResponse:
    # Determine language from file extension
    _, ext = os.path.splitext(request.fileName.lower())
    if ext not in {".ts", ".cpp"}:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file extension. Only .ts and .cpp are allowed.",
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        source_path = os.path.join(tmpdir, request.fileName)
        # Write the source code to a temporary file
        with open(source_path, "w", encoding="utf-8") as f:
            f.write(request.fileContent)

        if ext == ".ts":
            has_error, error_msg = _compile_typescript(source_path)
        else:  # .cpp
            has_error, error_msg = _compile_cpp(source_path)

        return CompileResponse(hasError=has_error, compilerError=error_msg)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)