import os
import re
import shutil
import subprocess
import tempfile
import threading
from contextlib import suppress
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field


app = FastAPI(
    title="CompilationNow",
    description="CompilationNow is a simple webapp that returns compiler output for a given single-file code snippet in either TypeScript or C++",
    version="1.0.0",
)

MAX_FILE_CONTENT_BYTES = 100_000
COMPILATION_TIMEOUT_SECONDS = 8
MAX_CONCURRENT_COMPILATIONS = 2
COMPILATION_SEMAPHORE = threading.BoundedSemaphore(MAX_CONCURRENT_COMPILATIONS)


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


def _safe_failure(message: str) -> CompileResponse:
    return CompileResponse(hasError=True, compilerError=message)


def _get_language(file_name: str) -> Optional[str]:
    lower_name = file_name.lower()
    if lower_name.endswith(".ts"):
        return "typescript"
    if lower_name.endswith(".cpp"):
        return "cpp"
    return None


def _sanitize_file_name(file_name: str) -> Optional[str]:
    if not isinstance(file_name, str):
        return None

    trimmed = file_name.strip()
    if not trimmed:
        return None

    base_name = os.path.basename(trimmed)
    if not base_name or base_name in {".", ".."}:
        return None

    if base_name != trimmed:
        return None

    if len(base_name) > 255:
        return None

    if not re.fullmatch(r"[A-Za-z0-9._-]+", base_name):
        return None

    return base_name


def _sanitize_compiler_output(output: str, file_name: str, temp_dir: str) -> str:
    sanitized = output or ""
    if temp_dir:
        sanitized = sanitized.replace(temp_dir, "")
    sanitized = sanitized.replace("\\", "/")

    abs_file = os.path.abspath(os.path.join(temp_dir, file_name)).replace("\\", "/")
    sanitized = sanitized.replace(abs_file, file_name)

    sanitized = re.sub(r"/tmp/[^:\s]+/", "", sanitized)
    sanitized = re.sub(r"[A-Za-z]:/[^:\s]+/", "", sanitized)

    lines = []
    for line in sanitized.splitlines():
        cleaned = line.strip()
        if cleaned:
            lines.append(cleaned)

    sanitized = "\n".join(lines).strip()
    if len(sanitized) > 4000:
        sanitized = sanitized[:4000].rstrip() + "..."
    return sanitized


def _run_compiler(command: list[str], working_dir: str, file_name: str) -> CompileResponse:
    try:
        result = subprocess.run(
            command,
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=COMPILATION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return _safe_failure("Compilation timed out.")
    except OSError:
        return _safe_failure("Compilation failed.")

    compiler_output = ((result.stdout or "") + (result.stderr or "")).strip()
    compiler_output = _sanitize_compiler_output(compiler_output, file_name, working_dir)

    return CompileResponse(
        hasError=result.returncode != 0,
        compilerError=compiler_output if compiler_output else None,
    )


def _compile_typescript(file_path: str, working_dir: str, file_name: str) -> CompileResponse:
    if shutil.which("tsc") is None:
        return _safe_failure("Compilation failed.")

    command = [
        "tsc",
        "--noEmit",
        "--pretty",
        "false",
        "--strict",
        file_path,
    ]
    return _run_compiler(command, working_dir, file_name)


def _compile_cpp(file_path: str, working_dir: str, file_name: str) -> CompileResponse:
    if shutil.which("g++") is None:
        return _safe_failure("Compilation failed.")

    output_binary = os.path.join(working_dir, "a.out")
    try:
        command = [
            "g++",
            "-std=c++17",
            "-fsyntax-only",
            file_path,
        ]
        return _run_compiler(command, working_dir, file_name)
    finally:
        with suppress(FileNotFoundError):
            os.remove(output_binary)


@app.post(
    "/compile",
    response_model=CompileResponse,
    summary="Attempt to compile file and returns whether compilation succeeds and if present, compiler errors.",
)
def compile_file(payload: CompileRequest) -> CompileResponse:
    safe_name = _sanitize_file_name(payload.fileName)
    if safe_name is None:
        return _safe_failure("Invalid fileName.")

    language = _get_language(safe_name)
    if language is None:
        return _safe_failure("Unsupported file extension. Only .ts and .cpp files are allowed.")

    if not isinstance(payload.fileContent, str):
        return _safe_failure("Invalid fileContent.")

    if len(payload.fileContent.encode("utf-8")) > MAX_FILE_CONTENT_BYTES:
        return _safe_failure("fileContent is too large.")

    acquired = COMPILATION_SEMAPHORE.acquire(blocking=False)
    if not acquired:
        return _safe_failure("Server is busy. Please try again later.")

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, safe_name)
            try:
                with open(file_path, "w", encoding="utf-8", newline="") as file_handle:
                    file_handle.write(payload.fileContent)
            except OSError:
                return _safe_failure("Compilation failed.")

            if language == "typescript":
                return _compile_typescript(file_path, temp_dir, safe_name)
            return _compile_cpp(file_path, temp_dir, safe_name)
    finally:
        COMPILATION_SEMAPHORE.release()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)