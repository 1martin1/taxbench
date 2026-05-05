import asyncio
import os
import re
import shutil
import subprocess
import tempfile
import threading
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, Field


TS_COMPILER_PATH = shutil.which("tsc")
CPP_COMPILER_PATH = shutil.which("g++")

MAX_FILE_CONTENT_BYTES = 100_000
MAX_COMPILER_OUTPUT_BYTES = 32_768
COMPILATION_TIMEOUT_SECONDS = 15
MAX_CONCURRENT_COMPILATIONS = 2

_compilation_semaphore = threading.BoundedSemaphore(value=MAX_CONCURRENT_COMPILATIONS)


class CompileRequest(BaseModel):
    fileName: str = Field(..., examples=["main.ts"])
    fileContent: str = Field(..., examples=["let x = 2 * 15;"])


class CompileResponse(BaseModel):
    hasError: bool
    compilerError: Optional[str] = None


def _validate_filename(file_name: str) -> str:
    if not file_name or file_name.strip() == "":
        raise HTTPException(status_code=400, detail="fileName must not be empty")

    if file_name.endswith(".ts"):
        return "typescript"
    if file_name.endswith(".cpp"):
        return "cpp"

    raise HTTPException(status_code=400, detail="Only .ts and .cpp files are supported")


def _safe_temp_filename(file_name: str) -> str:
    base_name = os.path.basename(file_name)
    if not base_name.strip():
        base_name = "source"
    sanitized = re.sub(r"[^A-Za-z0-9._-]", "_", base_name)
    if not sanitized or sanitized in {".", ".."}:
        sanitized = "source"
    return sanitized


def _ensure_content_size(file_content: str) -> None:
    size = len(file_content.encode("utf-8"))
    if size > MAX_FILE_CONTENT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"fileContent exceeds maximum allowed size of {MAX_FILE_CONTENT_BYTES} bytes",
        )


def _truncate_bytes(data: bytes, limit: int) -> bytes:
    if len(data) <= limit:
        return data
    return data[:limit]


def _read_limited_stream(stream, limit: int) -> bytes:
    chunks = []
    total = 0
    while True:
        remaining = limit - total
        if remaining <= 0:
            break
        chunk = stream.read(min(4096, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _sanitize_compiler_output(output: str, temp_dir: str, original_file_name: str, temp_file_name: str) -> str:
    sanitized = output.replace(temp_dir, "")
    sanitized = sanitized.replace(os.path.join(temp_dir, temp_file_name), original_file_name)
    sanitized = sanitized.replace(temp_file_name, original_file_name)
    sanitized = sanitized.replace("\\", "/")
    sanitized = re.sub(r"/tmp/[^:\s]+", "", sanitized)
    sanitized = re.sub(r"[A-Za-z]:/[^:\s]+", "", sanitized)
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized).strip()
    return sanitized


def _run_compiler(command: list[str], cwd: str) -> tuple[int, str]:
    acquired = _compilation_semaphore.acquire(timeout=1)
    if not acquired:
        raise HTTPException(status_code=503, detail="Compilation service is busy")

    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            stdout_data, stderr_data = process.communicate(timeout=COMPILATION_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                stdout_data, stderr_data = process.communicate(timeout=5)
            except Exception:
                stdout_data, stderr_data = b"", b""
            raise subprocess.TimeoutExpired(command, COMPILATION_TIMEOUT_SECONDS)

        combined = _truncate_bytes((stdout_data or b"") + (stderr_data or b""), MAX_COMPILER_OUTPUT_BYTES)
        output = combined.decode("utf-8", errors="replace").strip()
        return process.returncode, output
    finally:
        _compilation_semaphore.release()


def _compile_typescript(file_name: str, file_content: str) -> CompileResponse:
    if TS_COMPILER_PATH is None:
        raise HTTPException(status_code=500, detail="TypeScript compiler is not available")

    safe_file_name = _safe_temp_filename(file_name)
    if not safe_file_name.endswith(".ts"):
        safe_file_name = f"{safe_file_name}.ts"

    with tempfile.TemporaryDirectory() as temp_dir:
        file_path = os.path.join(temp_dir, safe_file_name)
        with open(file_path, "w", encoding="utf-8", newline="") as f:
            f.write(file_content)

        return_code, compiler_output = _run_compiler(
            [
                TS_COMPILER_PATH,
                "--noEmit",
                "--pretty",
                "false",
                safe_file_name,
            ],
            cwd=temp_dir,
        )

        if return_code == 0:
            return CompileResponse(hasError=False, compilerError=None)

        sanitized_output = _sanitize_compiler_output(compiler_output, temp_dir, file_name, safe_file_name)
        return CompileResponse(
            hasError=True,
            compilerError=sanitized_output or "TypeScript compilation failed",
        )


def _compile_cpp(file_name: str, file_content: str) -> CompileResponse:
    if CPP_COMPILER_PATH is None:
        raise HTTPException(status_code=500, detail="C++ compiler is not available")

    safe_file_name = _safe_temp_filename(file_name)
    if not safe_file_name.endswith(".cpp"):
        safe_file_name = f"{safe_file_name}.cpp"

    with tempfile.TemporaryDirectory() as temp_dir:
        file_path = os.path.join(temp_dir, safe_file_name)
        output_path = os.path.join(temp_dir, "a.out")

        with open(file_path, "w", encoding="utf-8", newline="") as f:
            f.write(file_content)

        return_code, compiler_output = _run_compiler(
            [
                CPP_COMPILER_PATH,
                "-fsyntax-only",
                safe_file_name,
                "-o",
                output_path,
            ],
            cwd=temp_dir,
        )

        if return_code == 0:
            return CompileResponse(hasError=False, compilerError=None)

        sanitized_output = _sanitize_compiler_output(compiler_output, temp_dir, file_name, safe_file_name)
        return CompileResponse(
            hasError=True,
            compilerError=sanitized_output or "C++ compilation failed",
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="CompilationNow",
    description="CompilationNow is a simple webapp that returns compiler output for a given single-file code snippet in either TypeScript or C++",
    version="1.0.0",
    lifespan=lifespan,
)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    compile_response_schema = (
        schema.get("components", {})
        .get("schemas", {})
        .get("CompileResponse", {})
    )
    properties = compile_response_schema.get("properties", {})
    if "compilerError" in properties:
        properties["compilerError"] = {
            "type": "string | null",
            "example": "background.ts:1:9 - error TS2304: Cannot find name 'y'.",
        }

    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi


@app.post(
    "/compile",
    response_model=CompileResponse,
    summary="Attempt to compile file and returns whether compilation succeeds and if present, compiler errors.",
)
async def compile_file(payload: CompileRequest) -> CompileResponse:
    language = _validate_filename(payload.fileName)
    _ensure_content_size(payload.fileContent)

    try:
        if language == "typescript":
            return await asyncio.to_thread(_compile_typescript, payload.fileName, payload.fileContent)
        return await asyncio.to_thread(_compile_cpp, payload.fileName, payload.fileContent)
    except subprocess.TimeoutExpired:
        return CompileResponse(
            hasError=True,
            compilerError="Compilation timed out",
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Compilation failed to start: {exc}") from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)