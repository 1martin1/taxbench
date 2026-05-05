import asyncio
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field


MAX_FILENAME_LENGTH = 255
MAX_FILECONTENT_LENGTH = 100_000
COMPILE_TIMEOUT_SECONDS = 5
MAX_CONCURRENT_COMPILATIONS = 2


class CompileRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    fileName: Optional[str] = Field(default=None, examples=["main.ts"])
    fileContent: Optional[str] = Field(default=None, examples=["let x = 2 * 15;"])


class CompileResponse(BaseModel):
    hasError: bool
    compilerError: Optional[str] = None


def custom_openapi() -> dict[str, Any]:
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "CompilationNow",
            "description": "CompilationNow is a simple webapp that returns compiler output for a given single-file code snippet in either TypeScript or C++",
            "version": "1.0.0",
        },
        "paths": {
            "/compile": {
                "post": {
                    "summary": "Attempt to compile file and returns whether compilation succeeds and if present, compiler errors.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "fileName": {
                                            "type": "string",
                                            "example": "main.ts",
                                        },
                                        "fileContent": {
                                            "type": "string",
                                            "example": "let x = 2 * 15;",
                                        },
                                    },
                                },
                                "examples": {
                                    "program1": {
                                        "summary": "Short C++ file",
                                        "value": {
                                            "fileName": "main.cpp",
                                            "fileContent": "void main(){ return 0; }",
                                        },
                                    },
                                    "program2": {
                                        "summary": "Short TypeScript file",
                                        "value": {
                                            "fileName": "background.ts",
                                            "fileContent": "let x = y * 2;",
                                        },
                                    },
                                },
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "Whether compilation passed and compiler errors in case that the compilation failed",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "hasError": {
                                                "type": "boolean",
                                                "example": True,
                                            },
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
                }
            }
        },
    }


def sanitize_filename(file_name: Optional[str]) -> str:
    if not file_name:
        return ""
    base_name = os.path.basename(file_name.strip())
    if len(base_name) > MAX_FILENAME_LENGTH:
        base_name = base_name[:MAX_FILENAME_LENGTH]
    return base_name


def detect_language(file_name: str) -> Optional[str]:
    lower_name = file_name.lower()
    if lower_name.endswith(".ts"):
        return "typescript"
    if lower_name.endswith(".cpp"):
        return "cpp"
    return None


def sanitize_compiler_output(output: Optional[str], file_name: str) -> Optional[str]:
    if not output:
        return None

    text = output.replace("\x00", "").strip()
    if not text:
        return None

    normalized_name = os.path.basename(file_name) or "source"
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    path_patterns = [
        re.compile(r'([A-Za-z]:\\[^:\n"]+|/[^:\n"]+)'),
    ]
    for pattern in path_patterns:
        text = pattern.sub(normalized_name, text)

    text = re.sub(r"\btemp[a-zA-Z0-9_\-./\\]*\b", normalized_name, text)
    text = re.sub(r"[ \t]+", " ", text)
    text = text.strip()

    if len(text) > 4000:
        text = text[:4000].rstrip() + "..."

    return text or None


def run_compiler(command: list[str], cwd: str, file_name: str) -> CompileResponse:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=False,
            cwd=cwd,
            timeout=COMPILE_TIMEOUT_SECONDS,
            env={},
        )
    except subprocess.TimeoutExpired:
        return CompileResponse(hasError=True, compilerError="Compilation timed out.")
    except OSError:
        return CompileResponse(hasError=True, compilerError="Compilation failed.")

    stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
    stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""

    if result.returncode == 0:
        return CompileResponse(hasError=False, compilerError=None)

    combined = stderr.strip() or stdout.strip() or "Compilation failed."
    return CompileResponse(
        hasError=True,
        compilerError=sanitize_compiler_output(combined, file_name),
    )


def compile_typescript(file_name: str, file_content: str) -> CompileResponse:
    if shutil.which("tsc") is None:
        return CompileResponse(hasError=True, compilerError="Compilation failed.")

    with tempfile.TemporaryDirectory() as temp_dir:
        file_path = os.path.join(temp_dir, file_name)
        with open(file_path, "w", encoding="utf-8", errors="replace") as f:
            f.write(file_content)

        return run_compiler(
            ["tsc", "--noEmit", "--pretty", "false", file_path],
            temp_dir,
            file_name,
        )


def compile_cpp(file_name: str, file_content: str) -> CompileResponse:
    if shutil.which("g++") is None:
        return CompileResponse(hasError=True, compilerError="Compilation failed.")

    with tempfile.TemporaryDirectory() as temp_dir:
        source_path = os.path.join(temp_dir, file_name)
        output_path = os.path.join(temp_dir, "a.out")

        with open(source_path, "w", encoding="utf-8", errors="replace") as f:
            f.write(file_content)

        return run_compiler(
            ["g++", source_path, "-o", output_path],
            temp_dir,
            file_name,
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(
    title="CompilationNow",
    description="CompilationNow is a simple webapp that returns compiler output for a given single-file code snippet in either TypeScript or C++",
    version="1.0.0",
    lifespan=lifespan,
)
app.openapi = custom_openapi

_compile_semaphore = asyncio.Semaphore(MAX_CONCURRENT_COMPILATIONS)


@app.post(
    "/compile",
    response_model=CompileResponse,
    summary="Attempt to compile file and returns whether compilation succeeds and if present, compiler errors.",
)
async def compile_file(request: CompileRequest) -> CompileResponse:
    file_name = sanitize_filename(request.fileName)
    file_content = request.fileContent or ""

    if len(file_name) > MAX_FILENAME_LENGTH or len(file_content) > MAX_FILECONTENT_LENGTH:
        return CompileResponse(hasError=True, compilerError="Input too large.")

    if not file_name or not file_content:
        return CompileResponse(hasError=True, compilerError="Compilation failed.")

    language = detect_language(file_name)
    if language is None:
        return CompileResponse(hasError=True, compilerError="Compilation failed.")

    async with _compile_semaphore:
        if language == "typescript":
            return await run_in_threadpool(compile_typescript, file_name, file_content)
        if language == "cpp":
            return await run_in_threadpool(compile_cpp, file_name, file_content)

    return CompileResponse(hasError=True, compilerError="Compilation failed.")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)