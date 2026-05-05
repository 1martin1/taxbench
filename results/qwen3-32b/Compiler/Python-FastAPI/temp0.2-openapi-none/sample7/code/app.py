from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import subprocess
import tempfile
import os

app = FastAPI()

class CompileRequest(BaseModel):
    fileName: str
    fileContent: str

class CompileResponse(BaseModel):
    hasError: bool
    compilerError: Optional[str]

@app.post("/compile", response_model=CompileResponse)
async def compile_code(request: CompileRequest):
    if not request.fileName.endswith(('.ts', '.cpp')):
        raise HTTPException(status_code=400, detail="Unsupported file type")
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = os.path.join(tmpdir, request.fileName)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w') as f:
            f.write(request.fileContent)
        if request.fileName.endswith('.ts'):
            command = ['tsc', file_path, '--noEmit']
        else:  # .cpp
            command = ['g++', '-fsyntax-only', file_path]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=tmpdir
        )
        if result.returncode != 0:
            error_msg = result.stderr.strip() or result.stdout.strip() or "Compilation failed without output"
            return {"hasError": True, "compilerError": error_msg}
        return {"hasError": False, "compilerError": None}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)