import os
import shutil
import subprocess
import tempfile
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field


CPP_EXTENSIONS = {".cpp", ".cc", ".cxx", ".c++", ".cp"}
TS_EXTENSIONS = {".ts"}


class CompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fileName: str = Field(..., examples=["main.ts"])
    fileContent: str = Field(..., examples=["let x = 2 * 15;"])


class CompileResponse(BaseModel):
    hasError: bool
    compilerError: Optional[str]


def detect_language(file_name: str) -> str:
    _, ext = os.path.splitext(file_name.lower())
    if ext in CPP_EXTENSIONS:
        return "cpp"
    if ext in TS_EXTENSIONS:
        return "ts"
    raise HTTPException(
        status_code=400,
        detail="Unsupported file extension. Only single-file TypeScript (.ts) and C++ (.cpp, .cc, .cxx, .c++, .cp) are supported.",
    )


def sanitize_filename(file_name: str) -> str:
    base_name = os.path.basename(file_name)
    if not base_name or base_name in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid fileName.")
    return base_name


def ensure_compiler_available(command: str) -> bool:
    return shutil.which(command) is not None


def compile_cpp(file_path: str) -> CompileResponse:
    if not ensure_compiler_available("g++"):
        raise HTTPException(status_code=500, detail="C++ compiler is not available on the server.")

    output_path = os.path.join(os.path.dirname(file_path), "a.out")
    result = subprocess.run(
        ["g++", "-std=c++17", "-fsyntax-only", file_path],
        capture_output=True,
        text=True,
        timeout=15,
    )

    compiler_output = (result.stderr or "") + (("\n" + result.stdout) if result.stdout else "")
    compiler_output = compiler_output.strip()

    return CompileResponse(
        hasError=result.returncode != 0,
        compilerError=compiler_output or None,
    )


def compile_typescript(file_path: str) -> CompileResponse:
    if not ensure_compiler_available("tsc"):
        raise HTTPException(status_code=500, detail="TypeScript compiler is not available on the server.")

    result = subprocess.run(
        ["tsc", "--noEmit", "--pretty", "false", file_path],
        capture_output=True,
        text=True,
        timeout=15,
    )

    compiler_output = (result.stdout or "") + (("\n" + result.stderr) if result.stderr else "")
    compiler_output = compiler_output.strip()

    return CompileResponse(
        hasError=result.returncode != 0,
        compilerError=compiler_output or None,
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


@app.post(
    "/compile",
    response_model=CompileResponse,
    summary="Attempt to compile file and returns whether compilation succeeds and if present, compiler errors.",
)
async def compile_file(request: CompileRequest) -> CompileResponse:
    file_name = sanitize_filename(request.fileName)
    language = detect_language(file_name)

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, file_name)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(request.fileContent)

            if language == "cpp":
                return compile_cpp(file_path)
            if language == "ts":
                return compile_typescript(file_path)

            raise HTTPException(status_code=400, detail="Unsupported language.")
    except subprocess.TimeoutExpired:
        return CompileResponse(
            hasError=True,
            compilerError="Compilation timed out.",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Internal server error: {exc}") from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)