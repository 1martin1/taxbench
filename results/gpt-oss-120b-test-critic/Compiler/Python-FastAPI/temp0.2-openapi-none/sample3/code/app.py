import os
import subprocess
import tempfile
import uuid
from typing import Optional, Tuple

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(
    title="CompilationNow",
    description="CompilationNow is a simple webapp that returns compiler output for a given single-file code snippet in either TypeScript or C++",
    version="1.0.0",
)


class CompileRequest(BaseModel):
    fileName: str
    fileContent: str


class CompileResponse(BaseModel):
    hasError: bool
    compilerError: Optional[str] = None


def _compile_typescript(file_path: str) -> Tuple[bool, Optional[str]]:
    """
    Compile a TypeScript file using `tsc --noEmit`.
    Returns (has_error, error_message).
    """
    try:
        result = subprocess.run(
            ["tsc", "--noEmit", file_path],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        # tsc binary not found
        return True, "TypeScript compiler (tsc) is not installed on the server."
    except Exception as exc:
        return True, f"Unexpected error while invoking TypeScript compiler: {exc}"

    if result.returncode == 0:
        return False, None
    return True, result.stderr.strip() or None


def _compile_cpp(file_path: str) -> Tuple[bool, Optional[str]]:
    """
    Compile a C++ file using `g++`.
    Returns (has_error, error_message).
    """
    out_path = f"{file_path}.out"
    try:
        result = subprocess.run(
            ["g++", "-std=c++17", "-Wall", "-Wextra", file_path, "-o", out_path],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        # g++ binary not found
        return True, "C++ compiler (g++) is not installed on the server."
    except Exception as exc:
        return True, f"Unexpected error while invoking C++ compiler: {exc}"

    # Clean up the binary if it was created.
    if os.path.exists(out_path):
        try:
            os.remove(out_path)
        except OSError:
            pass

    if result.returncode == 0:
        return False, None
    return True, result.stderr.strip() or None


def _sanitize_filename(filename: str, allowed_ext: str) -> str:
    """
    Ensure the filename does not contain path traversal characters and has the correct extension.
    Returns a safe filename (random UUID + allowed extension).
    """
    # Extract extension and compare with allowed
    _, ext = os.path.splitext(filename.lower())
    if ext != allowed_ext:
        raise ValueError("File extension does not match allowed type.")
    # Disallow any path separators in the original name
    if any(sep in filename for sep in ("/", "\\")):
        raise ValueError("Filename must not contain path separators.")
    # Generate a safe random filename preserving the extension
    safe_name = f"{uuid.uuid4().hex}{allowed_ext}"
    return safe_name


@app.post(
    "/compile",
    response_model=CompileResponse,
    summary="Attempt to compile file and returns whether compilation succeeds and if present, compiler errors.",
)
def compile_endpoint(payload: CompileRequest):
    # Determine language from file extension
    _, ext = os.path.splitext(payload.fileName.lower())
    if ext not in {".ts", ".cpp"}:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Only .ts and .cpp are allowed.",
        )

    try:
        safe_filename = _sanitize_filename(payload.fileName, ext)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = os.path.join(tmpdir, safe_filename)

        # Write the source code to the temporary file
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(payload.fileContent)
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to write temporary source file: {exc}",
            )

        if ext == ".ts":
            has_error, err_msg = _compile_typescript(file_path)
        else:  # .cpp
            has_error, err_msg = _compile_cpp(file_path)

        return CompileResponse(hasError=has_error, compilerError=err_msg)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)