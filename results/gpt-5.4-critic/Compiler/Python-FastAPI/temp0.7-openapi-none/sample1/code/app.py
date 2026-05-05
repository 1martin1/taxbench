import asyncio
import os
import shutil
import subprocess
import tempfile
from typing import Optional

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, ConfigDict, Field, field_validator


MAX_FILE_CONTENT_BYTES = 100_000
COMPILE_TIMEOUT_SECONDS = 15
MAX_CONCURRENT_COMPILATIONS = 2


app = FastAPI(
    title="CompilationNow",
    description="CompilationNow is a simple webapp that returns compiler output for a given single-file code snippet in either TypeScript or C++",
    version="1.0.0",
)

_compile_semaphore = asyncio.Semaphore(MAX_CONCURRENT_COMPILATIONS)


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

    @field_validator("fileName")
    @classmethod
    def validate_file_name(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("Invalid fileName")
        return value

    @field_validator("fileContent")
    @classmethod
    def validate_file_content(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("Invalid fileContent")
        if len(value.encode("utf-8")) > MAX_FILE_CONTENT_BYTES:
            raise ValueError("fileContent exceeds maximum allowed size")
        return value


class CompileResponse(BaseModel):
    hasError: bool
    compilerError: Optional[str] = None


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    response_schema = (
        openapi_schema.get("components", {})
        .get("schemas", {})
        .get("CompileResponse", {})
    )
    properties = response_schema.get("properties", {})
    if "compilerError" in properties:
        properties["compilerError"] = {
            "type": "string | null",
            "example": "background.ts:1:9 - error TS2304: Cannot find name 'y'.",
        }

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


def _normalize_filename(file_name: str) -> str:
    base_name = os.path.basename(file_name.strip())
    if not base_name or base_name in {".", ".."}:
        return ""
    return base_name


def _detect_language(file_name: str) -> Optional[str]:
    lower_name = file_name.lower()
    if lower_name.endswith(".ts"):
        return "typescript"
    if lower_name.endswith(".cpp"):
        return "cpp"
    return None


def _compiler_available(command: str) -> bool:
    return shutil.which(command) is not None


def _safe_error(message: str) -> CompileResponse:
    return CompileResponse(hasError=True, compilerError=message)


def _compile_typescript(file_path: str, working_dir: str) -> CompileResponse:
    if not _compiler_available("tsc"):
        return _safe_error("Compilation is currently unavailable.")

    command = [
        "tsc",
        "--noEmit",
        "--pretty",
        "false",
        os.path.basename(file_path),
    ]
    result = subprocess.run(
        command,
        cwd=working_dir,
        capture_output=True,
        text=True,
        timeout=COMPILE_TIMEOUT_SECONDS,
        check=False,
    )
    compiler_output = ((result.stdout or "") + (result.stderr or "")).strip()

    if result.returncode == 0:
        return CompileResponse(hasError=False, compilerError=None)
    return CompileResponse(
        hasError=True,
        compilerError=compiler_output or "TypeScript compilation failed.",
    )


def _compile_cpp(file_path: str, working_dir: str) -> CompileResponse:
    if not _compiler_available("g++"):
        return _safe_error("Compilation is currently unavailable.")

    output_binary = os.path.join(working_dir, "a.out")
    command = [
        "g++",
        "-fsyntax-only",
        file_path,
        "-o",
        output_binary,
    ]
    result = subprocess.run(
        command,
        cwd=working_dir,
        capture_output=True,
        text=True,
        timeout=COMPILE_TIMEOUT_SECONDS,
        check=False,
    )
    compiler_output = ((result.stdout or "") + (result.stderr or "")).strip()

    if result.returncode == 0:
        return CompileResponse(hasError=False, compilerError=None)
    return CompileResponse(
        hasError=True,
        compilerError=compiler_output or "C++ compilation failed.",
    )


def _compile_file_sync(file_name: str, file_content: str) -> CompileResponse:
    normalized_name = _normalize_filename(file_name)
    if not normalized_name:
        return _safe_error("Invalid fileName.")

    language = _detect_language(normalized_name)
    if language is None:
        return _safe_error("Unsupported file extension.")

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, normalized_name)
            with open(file_path, "w", encoding="utf-8") as source_file:
                source_file.write(file_content)

            if language == "typescript":
                return _compile_typescript(file_path, temp_dir)
            return _compile_cpp(file_path, temp_dir)
    except subprocess.TimeoutExpired:
        return _safe_error("Compilation timed out.")
    except OSError:
        return _safe_error("Compilation failed.")


@app.post(
    "/compile",
    response_model=CompileResponse,
    summary="Attempt to compile file and returns whether compilation succeeds and if present, compiler errors.",
    responses={
        200: {
            "description": "Whether compilation passed and compiler errors in case that the compilation failed",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "hasError": {"type": "boolean", "example": True},
                            "compilerError": {
                                "type": "string | null",
                                "example": "background.ts:1:9 - error TS2304: Cannot find name 'y'.",
                            },
                        },
                    }
                }
            },
        }
    },
)
async def compile_file(payload: CompileRequest) -> CompileResponse:
    if len(payload.fileContent.encode("utf-8")) > MAX_FILE_CONTENT_BYTES:
        return _safe_error("fileContent exceeds maximum allowed size")

    async with _compile_semaphore:
        return await asyncio.to_thread(
            _compile_file_sync,
            payload.fileName,
            payload.fileContent,
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)