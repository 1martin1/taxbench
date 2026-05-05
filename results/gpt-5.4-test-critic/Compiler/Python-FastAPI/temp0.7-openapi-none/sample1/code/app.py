import os
import shutil
import subprocess
import tempfile
from typing import Optional

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


def normalize_error_output(output: str) -> Optional[str]:
    cleaned = output.strip()
    return cleaned if cleaned else None


def validate_filename(file_name: str) -> str:
    if not file_name or file_name.strip() == "":
        raise HTTPException(status_code=400, detail="fileName must not be empty")

    base_name = os.path.basename(file_name)
    if base_name != file_name:
        raise HTTPException(status_code=400, detail="fileName must not contain path components")

    if file_name.endswith(".cpp"):
        return "cpp"
    if file_name.endswith(".ts"):
        return "ts"

    raise HTTPException(status_code=400, detail="Only .cpp and .ts files are supported")


def compile_cpp(file_path: str, file_name: str) -> CompileResponse:
    compiler = shutil.which("g++")
    if compiler is None:
        raise HTTPException(status_code=500, detail="C++ compiler is not available")

    output_binary = os.path.join(os.path.dirname(file_path), "a.out")
    process = subprocess.run(
        [compiler, "-fsyntax-only", file_path],
        capture_output=True,
        text=True,
        timeout=15,
    )

    stderr = normalize_error_output(process.stderr)
    stdout = normalize_error_output(process.stdout)
    combined = "\n".join(part for part in [stderr, stdout] if part)

    if process.returncode == 0:
        return CompileResponse(hasError=False, compilerError=None)

    return CompileResponse(hasError=True, compilerError=combined or f"{file_name}: compilation failed")


def compile_typescript(file_path: str, file_name: str) -> CompileResponse:
    tsc = shutil.which("tsc")
    if tsc is None:
        raise HTTPException(status_code=500, detail="TypeScript compiler is not available")

    process = subprocess.run(
        [tsc, "--noEmit", "--pretty", "false", file_path],
        capture_output=True,
        text=True,
        timeout=15,
    )

    stderr = normalize_error_output(process.stderr)
    stdout = normalize_error_output(process.stdout)
    combined = "\n".join(part for part in [stdout, stderr] if part)

    if process.returncode == 0:
        return CompileResponse(hasError=False, compilerError=None)

    return CompileResponse(hasError=True, compilerError=combined or f"{file_name}: compilation failed")


@app.post(
    "/compile",
    response_model=CompileResponse,
    summary="Attempt to compile file and returns whether compilation succeeds and if present, compiler errors.",
)
def compile_code(payload: CompileRequest) -> CompileResponse:
    language = validate_filename(payload.fileName)

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, payload.fileName)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(payload.fileContent)

            if language == "cpp":
                return compile_cpp(file_path, payload.fileName)
            return compile_typescript(file_path, payload.fileName)
    except subprocess.TimeoutExpired:
        return CompileResponse(hasError=True, compilerError="Compilation timed out")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Server error while handling file: {exc}") from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)