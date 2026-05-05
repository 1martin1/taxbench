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
    hasError: bool
    compilerError: Optional[str]


def _normalize_filename(file_name: str) -> str:
    if not file_name or not file_name.strip():
        raise HTTPException(status_code=400, detail="fileName must not be empty")

    base_name = os.path.basename(file_name.strip())
    if base_name in {"", ".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid fileName")

    return base_name


def _detect_language(file_name: str) -> str:
    lower_name = file_name.lower()
    if lower_name.endswith(".ts"):
        return "typescript"
    if lower_name.endswith(".cpp"):
        return "cpp"
    raise HTTPException(
        status_code=400,
        detail="Unsupported file extension. Only .ts and .cpp are supported.",
    )


def _readable_error(stderr: str, stdout: str) -> Optional[str]:
    text = (stderr or "").strip()
    if text:
        return text
    text = (stdout or "").strip()
    if text:
        return text
    return None


def _compile_typescript(work_dir: str, file_name: str) -> CompileResponse:
    if shutil.which("tsc") is None:
        raise HTTPException(status_code=500, detail="TypeScript compiler not installed")

    cmd = [
        "tsc",
        "--noEmit",
        "--pretty",
        "false",
        file_name,
    ]

    result = subprocess.run(
        cmd,
        cwd=work_dir,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    compiler_error = _readable_error(result.stderr, result.stdout)
    has_error = result.returncode != 0

    return CompileResponse(
        hasError=has_error,
        compilerError=compiler_error if has_error else None,
    )


def _compile_cpp(work_dir: str, file_name: str) -> CompileResponse:
    compiler = shutil.which("g++")
    if compiler is None:
        raise HTTPException(status_code=500, detail="C++ compiler not installed")

    output_path = os.path.join(work_dir, "a.out")
    cmd = [
        compiler,
        "-std=c++17",
        "-fsyntax-only",
        file_name,
        "-o",
        output_path,
    ]

    result = subprocess.run(
        cmd,
        cwd=work_dir,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    compiler_error = _readable_error(result.stderr, result.stdout)
    has_error = result.returncode != 0

    return CompileResponse(
        hasError=has_error,
        compilerError=compiler_error if has_error else None,
    )


@app.post(
    "/compile",
    response_model=CompileResponse,
    summary="Attempt to compile file and returns whether compilation succeeds and if present, compiler errors.",
)
def compile_file(payload: CompileRequest) -> CompileResponse:
    file_name = _normalize_filename(payload.fileName)
    language = _detect_language(file_name)

    try:
        with tempfile.TemporaryDirectory(prefix="compilationnow_") as work_dir:
            file_path = os.path.join(work_dir, file_name)
            with open(file_path, "w", encoding="utf-8") as file_handle:
                file_handle.write(payload.fileContent)

            if language == "typescript":
                return _compile_typescript(work_dir, file_name)
            if language == "cpp":
                return _compile_cpp(work_dir, file_name)

            raise HTTPException(status_code=400, detail="Unsupported language")
    except subprocess.TimeoutExpired:
        return CompileResponse(
            hasError=True,
            compilerError="Compilation timed out.",
        )
    except HTTPException:
        raise
    except Exception as exc:
        with suppress(Exception):
            _ = str(exc)
        raise HTTPException(status_code=500, detail="Internal server error")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)