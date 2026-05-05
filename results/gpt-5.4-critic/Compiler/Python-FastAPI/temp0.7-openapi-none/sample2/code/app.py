import asyncio
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import suppress
from typing import Optional

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_FILE_CONTENT_BYTES = 100_000
MAX_FILENAME_LENGTH = 255
SUBPROCESS_TIMEOUT_SECONDS = 10
MAX_CONCURRENT_COMPILATIONS = 2
ERROR_MESSAGE_MAX_LENGTH = 4000

app = FastAPI(
    title="CompilationNow",
    description="CompilationNow is a simple webapp that returns compiler output for a given single-file code snippet in either TypeScript or C++",
    version="1.0.0",
)

_compile_semaphore = asyncio.Semaphore(MAX_CONCURRENT_COMPILATIONS)


class CompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fileName: str = Field(..., examples=["main.ts"])
    fileContent: str = Field(..., examples=["let x = 2 * 15;"])

    @field_validator("fileName")
    @classmethod
    def validate_file_name(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("fileName must be a string")
        if not value.strip():
            raise ValueError("fileName must not be empty")
        if len(value) > MAX_FILENAME_LENGTH:
            raise ValueError("fileName is too long")
        return value

    @field_validator("fileContent")
    @classmethod
    def validate_file_content(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("fileContent must be a string")
        if len(value.encode("utf-8")) > MAX_FILE_CONTENT_BYTES:
            raise ValueError("fileContent is too large")
        return value


class CompileResponse(BaseModel):
    hasError: bool
    compilerError: Optional[str]


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    compile_post = schema.get("paths", {}).get("/compile", {}).get("post", {})
    responses = compile_post.get("responses", {})
    response_200 = responses.get("200", {})
    content = response_200.get("content", {})
    app_json = content.get("application/json", {})
    resp_schema = app_json.get("schema", {})
    properties = resp_schema.get("properties", {})
    if "compilerError" in properties:
        properties["compilerError"] = {
            "nullable": True,
            "example": "background.ts:1:9 - error TS2304: Cannot find name 'y'.",
            "type": "string | null",
        }

    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi


def _normalize_filename(file_name: str) -> str:
    name = os.path.basename(file_name.strip())
    if not name:
        return ""
    if len(name) > MAX_FILENAME_LENGTH:
        return ""
    if "/" in name or "\\" in name:
        return ""
    if name in {".", ".."}:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        return ""
    return name


def _detect_language(file_name: str) -> Optional[str]:
    lowered = file_name.lower()
    if lowered.endswith(".ts"):
        return "typescript"
    if lowered.endswith(".cpp"):
        return "cpp"
    return None


def _sanitize_output(output: Optional[str], file_name: str) -> Optional[str]:
    if not output:
        return None

    sanitized = output.replace("\r\n", "\n").replace("\r", "\n")
    sanitized = sanitized.replace(os.getcwd(), "")
    sanitized = re.sub(r"/tmp/[^\s:]+", "", sanitized)
    sanitized = re.sub(r"[A-Za-z]:\\\\[^\s:]+", "", sanitized)
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized).strip()

    allowed_lines = []
    for line in sanitized.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if file_name in stripped or "error" in stripped.lower() or "warning" in stripped.lower():
            allowed_lines.append(stripped)

    if not allowed_lines:
        return "Compilation failed"

    result = "\n".join(allowed_lines)
    if len(result) > ERROR_MESSAGE_MAX_LENGTH:
        result = result[:ERROR_MESSAGE_MAX_LENGTH].rstrip() + "..."
    return result


def _run_command(command: list[str], cwd: str, file_name: str) -> tuple[int, Optional[str]]:
    executable = shutil.which(command[0])
    if executable is None:
        return 127, "Compiler unavailable"

    try:
        completed = subprocess.run(
            [executable, *command[1:]],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, "Compilation timed out"
    except Exception:
        return 125, "Compilation failed"

    output_parts = []
    if completed.stdout:
        output_parts.append(completed.stdout)
    if completed.stderr:
        output_parts.append(completed.stderr)

    output = "".join(output_parts).strip()
    return completed.returncode, _sanitize_output(output, file_name)


def _compile_typescript(file_name: str, work_dir: str) -> CompileResponse:
    command = [
        "tsc",
        "--noEmit",
        "--pretty",
        "false",
        "--skipLibCheck",
        file_name,
    ]
    return_code, output = _run_command(command, work_dir, file_name)
    if return_code == 0:
        return CompileResponse(hasError=False, compilerError=None)
    return CompileResponse(hasError=True, compilerError=output or "TypeScript compilation failed")


def _compile_cpp(file_name: str, work_dir: str) -> CompileResponse:
    command = [
        "g++",
        "-fsyntax-only",
        file_name,
    ]
    return_code, output = _run_command(command, work_dir, file_name)
    if return_code == 0:
        return CompileResponse(hasError=False, compilerError=None)
    return CompileResponse(hasError=True, compilerError=output or "C++ compilation failed")


@app.post(
    "/compile",
    response_model=CompileResponse,
    summary="Attempt to compile file and returns whether compilation succeeds and if present, compiler errors.",
)
async def compile_file(payload: CompileRequest) -> CompileResponse:
    file_name = _normalize_filename(payload.fileName)
    language = _detect_language(file_name) if file_name else None

    if not file_name or language is None:
        return CompileResponse(hasError=True, compilerError="Unsupported or invalid fileName")

    async with _compile_semaphore:
        work_dir = tempfile.mkdtemp(prefix="compilationnow_")
        try:
            source_path = os.path.join(work_dir, file_name)
            with open(source_path, "w", encoding="utf-8", newline="\n") as source_file:
                source_file.write(payload.fileContent)

            if language == "typescript":
                return await asyncio.to_thread(_compile_typescript, file_name, work_dir)
            return await asyncio.to_thread(_compile_cpp, file_name, work_dir)
        finally:
            with suppress(Exception):
                shutil.rmtree(work_dir)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)