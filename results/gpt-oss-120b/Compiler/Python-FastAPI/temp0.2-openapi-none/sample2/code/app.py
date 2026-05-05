import os
import pathlib
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


def _run_typescript(source_path: str) -> tuple[bool, Optional[str]]:
    """Compile a TypeScript file using `tsc` with no emit."""
    try:
        result = subprocess.run(
            ["tsc", source_path, "--noEmit", "--pretty", "false"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        return True, f"Failed to run tsc: {exc}"

    if result.returncode == 0:
        return False, None
    # tsc writes diagnostics to stderr
    return True, result.stderr.strip() or None


def _run_cpp(source_path: str) -> tuple[bool, Optional[str]]:
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
        )
    except Exception as exc:
        return True, f"Failed to run g++: {exc}"

    if result.returncode == 0:
        return False, None
    return True, result.stderr.strip() or None


@app.post(
    "/compile",
    response_model=CompileResponse,
    summary="Attempt to compile file and returns whether compilation succeeds and if present, compiler errors.",
)
def compile_code(request: CompileRequest) -> CompileResponse:
    ext = pathlib.Path(request.fileName).suffix.lower()
    supported_ts = {".ts", ".tsx"}
    supported_cpp = {".cpp", ".cc", ".cxx", ".c"}

    if ext not in supported_ts.union(supported_cpp):
        raise HTTPException(
            status_code=400, detail="Unsupported file extension"
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        # Preserve possible sub‑directories in the filename
        target_path = os.path.join(tmpdir, request.fileName)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)

        with open(target_path, "w", encoding="utf-8") as f:
            f.write(request.fileContent)

        if ext in supported_ts:
            has_error, err = _run_typescript(target_path)
        else:
            has_error, err = _run_cpp(target_path)

        return CompileResponse(hasError=has_error, compilerError=err)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)