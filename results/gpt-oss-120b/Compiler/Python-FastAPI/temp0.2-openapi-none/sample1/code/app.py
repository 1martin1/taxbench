import os
import subprocess
import tempfile
from pathlib import Path
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


def _run_typescript(file_path: Path) -> (bool, Optional[str]):
    """Compile TypeScript file using `tsc --noEmit`."""
    try:
        result = subprocess.run(
            ["tsc", "--noEmit", str(file_path)],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="TypeScript compiler (tsc) not found.")
    has_error = result.returncode != 0
    error_msg = result.stderr.strip() if has_error else None
    return has_error, error_msg


def _run_cpp(file_path: Path) -> (bool, Optional[str]):
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
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="C++ compiler (g++) not found.")
    has_error = result.returncode != 0
    error_msg = result.stderr.strip() if has_error else None
    return has_error, error_msg


@app.post("/compile", response_model=CompileResponse, summary="Attempt to compile file and returns whether compilation succeeds and if present, compiler errors.")
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