import os
import shutil
import subprocess
import tempfile
from contextlib import suppress
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field


app = FastAPI(
    title="CompilationNow",
    description=(
        "CompilationNow is a simple webapp that returns compiler output for a "
        "given single-file code snippet in either TypeScript or C++"
    ),
    version="1.0.0",
)


class CompileRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "fileName": "main.cpp",
                    "fileContent": "void main(){ return 0; }",
                },
                {
                    "fileName": "background.ts",
                    "fileContent": "let x = y * 2;",
                },
            ]
        }
    )

    fileName: str = Field(..., examples=["main.ts"])
    fileContent: str = Field(..., examples=["let x = 2 * 15;"])


class CompileResponse(BaseModel):
    hasError: bool = Field(..., examples=[True])
    compilerError: Optional[str] = Field(
        ...,
        examples=["background.ts:1:9 - error TS2304: Cannot find name 'y'."],
    )


def _normalize_error_output(stderr: str, stdout: str) -> str:
    combined = "\n".join(part for part in [stderr.strip(), stdout.strip()] if part).strip()
    return combined if combined else "Compilation failed with no compiler output."


def _ensure_supported_extension(file_name: str) -> str:
    _, ext = os.path.splitext(file_name)
    ext = ext.lower()
    if ext not in {".cpp", ".ts"}:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file extension. Only .cpp and .ts are allowed.",
        )
    return ext


def _safe_filename(file_name: str) -> str:
    base_name = os.path.basename(file_name)
    if not base_name or base_name in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid fileName.")
    return base_name


def _compile_cpp(file_path: str) -> CompileResponse:
    compiler = shutil.which("g++")
    if compiler is None:
        raise HTTPException(status_code=500, detail="C++ compiler is not available.")

    output_binary = os.path.join(os.path.dirname(file_path), "a.out")
    result = subprocess.run(
        [compiler, "-fsyntax-only", file_path],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    with suppress(FileNotFoundError):
        os.remove(output_binary)

    if result.returncode == 0:
        return CompileResponse(hasError=False, compilerError=None)

    return CompileResponse(
        hasError=True,
        compilerError=_normalize_error_output(result.stderr, result.stdout),
    )


def _compile_typescript(file_path: str) -> CompileResponse:
    tsc = shutil.which("tsc")
    if tsc is None:
        raise HTTPException(status_code=500, detail="TypeScript compiler is not available.")

    result = subprocess.run(
        [tsc, "--noEmit", file_path],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    if result.returncode == 0:
        return CompileResponse(hasError=False, compilerError=None)

    return CompileResponse(
        hasError=True,
        compilerError=_normalize_error_output(result.stderr, result.stdout),
    )


@app.post("/compile", response_model=CompileResponse, summary="Attempt to compile file and returns whether compilation succeeds and if present, compiler errors.")
def compile_file(payload: CompileRequest) -> CompileResponse:
    file_name = _safe_filename(payload.fileName)
    extension = _ensure_supported_extension(file_name)

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, file_name)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(payload.fileContent)

            if extension == ".cpp":
                return _compile_cpp(file_path)

            return _compile_typescript(file_path)
    except subprocess.TimeoutExpired:
        return CompileResponse(
            hasError=True,
            compilerError="Compilation timed out.",
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)