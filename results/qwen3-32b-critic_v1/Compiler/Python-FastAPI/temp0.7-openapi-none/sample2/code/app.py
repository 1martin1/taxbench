import os
import subprocess
import tempfile
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, field_validator
from typing import Optional

app = FastAPI()

class CompileRequest(BaseModel):
    fileName: str
    fileContent: str

    @field_validator('fileName')
    def validate_file_name_length(cls, v):
        if len(v) > 255:
            raise ValueError("File name too long")
        return v

    @field_validator('fileContent')
    def validate_file_content_length(cls, v):
        if len(v) > 1024 * 1024:  # 1MB
            raise ValueError("File content too large")
        return v

class CompileResponse(BaseModel):
    hasError: bool
    compilerError: Optional[str]

@app.post("/compile", response_model=CompileResponse)
async def compile_code(request: CompileRequest):
    if not request.fileName:
        raise HTTPException(status_code=400, detail="File name is required")
    _, ext = os.path.splitext(request.fileName)
    if ext not in ('.cpp', '.ts'):
        raise HTTPException(status_code=400, detail="Unsupported file type")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_abs = os.path.abspath(tmpdir)
        file_path = os.path.join(tmpdir, request.fileName)
        file_abs = os.path.abspath(file_path)
        
        # Prevent path traversal attacks
        if not file_abs.startswith(f"{tmpdir_abs}{os.path.sep}"):
            raise HTTPException(status_code=400, detail="Invalid file name")

        try:
            with open(file_path, 'w') as f:
                f.write(request.fileContent)
        except Exception as e:
            raise HTTPException(status_code=500, detail="File write error") from e

        if ext == '.cpp':
            cmd = ['g++', request.fileName, '-o', os.path.join(tmpdir, 'output')]
        else:  # .ts
            cmd = ['tsc', request.fileName, '--noEmit']

        try:
            result = subprocess.run(
                cmd,
                cwd=tmpdir,
                capture_output=True,
                text=True,
                check=False,
                timeout=10  # 10-second timeout
            )
        except FileNotFoundError:
            raise HTTPException(status_code=500, detail=f"Compiler for {ext} not found")
        except subprocess.TimeoutExpired:
            return CompileResponse(
                hasError=True,
                compilerError="Compilation timed out"
            )

        return CompileResponse(
            hasError=bool(result.returncode),
            compilerError=result.stderr if result.returncode else None
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)