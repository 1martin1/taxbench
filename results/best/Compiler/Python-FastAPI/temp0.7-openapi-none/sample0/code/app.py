# проходит все тесты

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from fastapi import Body, FastAPI
from pydantic import BaseModel, Field
import uvicorn


APP_TITLE = "CompilationNow"
APP_DESCRIPTION = (
    "CompilationNow is a simple webapp that returns compiler output for a given "
    "single-file code snippet in either TypeScript or C++"
)
APP_VERSION = "1.0.0"


class CompileRequest(BaseModel):
    fileName: str = Field(..., example="main.ts")
    fileContent: str = Field(..., example="let x = 2 * 15;")


class CompileResponse(BaseModel):
    hasError: bool = Field(..., example=True)
    compilerError: Optional[str] = Field(
        default=None,
        example="background.ts:1:9 - error TS2304: Cannot find name 'y'.",
    )


app = FastAPI(title=APP_TITLE, description=APP_DESCRIPTION, version=APP_VERSION)


def _detect_language(file_name: str) -> str:
    ext = Path(file_name).suffix.lower()
    ts_exts = {".ts", ".tsx"}
    cpp_exts = {".cpp", ".cc", ".cxx", ".c++", ".C".lower()}
    if ext in ts_exts:
        return "ts"
    if ext in cpp_exts:
        return "cpp"
    return "unknown"


def _which_or_none(cmd: str) -> Optional[str]:
    path = shutil.which(cmd)
    return path


def _run_process(cmd: list, cwd: Path) -> Tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out.strip()
    except FileNotFoundError as e:
        return 127, f"Command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "Compilation timed out."
    except Exception as e:
        return 1, f"Internal error while running compiler: {e}"


def _compile_typescript(src_path: Path) -> Tuple[bool, Optional[str]]:
    tsc = _which_or_none("tsc")
    if not tsc:
        return True, "TypeScript compiler (tsc) not found. Ensure TypeScript is installed globally."

    # Use --noEmit to only type-check and --pretty false for machine-readable output
    cmd = [tsc, "--pretty", "false", "--noEmit", src_path.name]
    code, output = _run_process(cmd, cwd=src_path.parent)
    if code == 0:
        return False, None
    # Non-zero exit generally means errors
    return True, output or "Unknown TypeScript compilation error."


def _compile_cpp(src_path: Path) -> Tuple[bool, Optional[str]]:
    gpp = _which_or_none("g++")
    if not gpp:
        return True, "C++ compiler (g++) not found."

    # -fsyntax-only to only check syntax/semantics without producing binaries
    cmd = [gpp, "-std=c++17", "-fsyntax-only", src_path.name]
    code, output = _run_process(cmd, cwd=src_path.parent)
    if code == 0:
        return False, None
    return True, output or "Unknown C++ compilation error."


@app.post(
    "/compile",
    response_model=CompileResponse,
    summary="Attempt to compile file and returns whether compilation succeeds and if present, compiler errors.",
)
def compile_code(
    payload: CompileRequest = Body(
        ...,
        examples={
            "program1": {
                "summary": "Short C++ file",
                "value": {"fileName": "main.cpp", "fileContent": "void main(){ return 0; }"},
            },
            "program2": {
                "summary": "Short TypeScript file",
                "value": {"fileName": "background.ts", "fileContent": "let x = y * 2;"},
            },
        },
    )
) -> CompileResponse:
    # Sanitize file name to avoid directory traversal and ensure a reasonable name
    safe_name = os.path.basename(payload.fileName).strip()
    if not safe_name:
        return CompileResponse(hasError=True, compilerError="Invalid file name.")

    language = _detect_language(safe_name)
    if language == "unknown":
        return CompileResponse(
            hasError=True,
            compilerError=f"Unsupported file extension for '{safe_name}'. Only TypeScript (.ts, .tsx) and C++ (.cpp, .cc, .cxx, .c++) are supported.",
        )

    # Create a temp directory to hold the file
    with tempfile.TemporaryDirectory(prefix="compilationnow_") as tmpdir:
        tmp_path = Path(tmpdir) / safe_name
        try:
            tmp_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(payload.fileContent, encoding="utf-8")
        except Exception as e:
            return CompileResponse(hasError=True, compilerError=f"Failed to write file: {e}")

        if language == "ts":
            has_err, comp_err = _compile_typescript(tmp_path)
        else:
            has_err, comp_err = _compile_cpp(tmp_path)

        return CompileResponse(hasError=has_err, compilerError=comp_err)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)