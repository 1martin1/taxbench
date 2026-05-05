import os
import shutil
import subprocess
import tempfile
from contextlib import suppress
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field


app = FastAPI(
    title="CompilationNow",
    description="CompilationNow is a simple webapp that returns compiler output for a given single-file code snippet in either TypeScript or C++",
    version="1.0.0",
)


class CompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fileName: str = Field(..., examples=["main.ts"])
    fileContent: str = Field(..., examples=["let x = 2 * 15;"])


class CompileResponse(BaseModel):
    hasError: bool
    compilerError: Optional[str]


def _normalize_error_output(output: str, file_name: str) -> str:
    output = output.strip()
    if not output:
        return output
    return output.replace("\\", "/")


def _compile_typescript(file_path: str, file_name: str) -> CompileResponse:
    tsc_path = shutil.which("tsc")
    if not tsc_path:
        raise HTTPException(status_code=500, detail="TypeScript compiler is not installed")

    result = subprocess.run(
        [
            tsc_path,
            "--noEmit",
            "--pretty",
            "false",
            file_path,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    combined_output = (result.stdout or "") + (result.stderr or "")
    combined_output = _normalize_error_output(combined_output, file_name)

    if result.returncode == 0:
        return CompileResponse(hasError=False, compilerError=None)

    return CompileResponse(
        hasError=True,
        compilerError=combined_output or "TypeScript compilation failed",
    )


def _compile_cpp(file_path: str, file_name: str) -> CompileResponse:
    gpp_path = shutil.which("g++")
    if not gpp_path:
        raise HTTPException(status_code=500, detail="g++ compiler is not installed")

    output_binary = os.path.join(os.path.dirname(file_path), "a.out")
    result = subprocess.run(
        [
            gpp_path,
            "-fsyntax-only",
            file_path,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    with suppress(FileNotFoundError):
        os.remove(output_binary)

    combined_output = (result.stdout or "") + (result.stderr or "")
    combined_output = _normalize_error_output(combined_output, file_name)

    if result.returncode == 0:
        return CompileResponse(hasError=False, compilerError=None)

    return CompileResponse(
        hasError=True,
        compilerError=combined_output or "C++ compilation failed",
    )


def _validate_filename(file_name: str) -> str:
    if not file_name or file_name.strip() == "":
        raise HTTPException(status_code=400, detail="fileName must not be empty")

    base_name = os.path.basename(file_name)
    if base_name != file_name:
        raise HTTPException(status_code=400, detail="fileName must not contain path components")

    lowered = file_name.lower()
    if lowered.endswith(".ts"):
        return "ts"
    if lowered.endswith(".cpp"):
        return "cpp"

    raise HTTPException(status_code=400, detail="Only .ts and .cpp files are supported")


@app.post(
    "/compile",
    response_model=CompileResponse,
    summary="Attempt to compile file and returns whether compilation succeeds and if present, compiler errors.",
)
def compile_file(payload: CompileRequest) -> CompileResponse:
    language = _validate_filename(payload.fileName)

    temp_dir = tempfile.mkdtemp(prefix="compilationnow_")
    try:
        file_path = os.path.join(temp_dir, payload.fileName)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(payload.fileContent)

        try:
            if language == "ts":
                return _compile_typescript(file_path, payload.fileName)
            return _compile_cpp(file_path, payload.fileName)
        except subprocess.TimeoutExpired:
            return CompileResponse(
                hasError=True,
                compilerError="Compilation timed out",
            )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)