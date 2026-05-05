import os
import subprocess
import tempfile
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

app = FastAPI()

class CompileRequest(BaseModel):
    fileName: str
    fileContent: str

class CompileResponse(BaseModel):
    hasError: bool
    compilerError: Optional[str]

def is_valid_filename(filename: str) -> bool:
    """Validate filename for security and correctness."""
    if not filename:
        return False
    if '/' in filename or '\\' in filename:
        return False
    if '..' in filename:
        return False
    if filename.startswith('-'):
        return False
    if ' ' in filename:
        return False
    # Allow alphanumeric, underscores, dots, and hyphens (but not starting with hyphen)
    if not all(c.isalnum() or c in ('_', '.', '-') for c in filename):
        return False
    return True

def compile_code(request: CompileRequest) -> CompileResponse:
    if not is_valid_filename(request.fileName):
        return CompileResponse(hasError=True, compilerError="Invalid file name: contains forbidden characters or patterns")
    
    ext = os.path.splitext(request.fileName)[1].lower()
    if ext not in ('.cpp', '.ts'):
        return CompileResponse(hasError=True, compilerError="Unsupported file type. Use .cpp or .ts")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = os.path.join(tmpdir, request.fileName)
        try:
            with open(filepath, 'w') as f:
                f.write(request.fileContent)
        except Exception as e:
            return CompileResponse(hasError=True, compilerError=f"File write error: {str(e)}")
        
        try:
            if ext == '.cpp':
                result = subprocess.run(
                    ['g++', '-c', '-std=c++17', filepath],
                    capture_output=True,
                    text=True,
                    cwd=tmpdir
                )
            else:  # .ts
                result = subprocess.run(
                    ['tsc', '--noEmit', '--pretty', 'false', request.fileName],
                    capture_output=True,
                    text=True,
                    cwd=tmpdir
                )
        except OSError as e:
            return CompileResponse(hasError=True, compilerError=f"Compiler not found: {str(e)}")
        
        has_error = result.returncode != 0
        error_output = (result.stdout + result.stderr).strip() or None
        return CompileResponse(hasError=has_error, compilerError=error_output)

@app.post("/compile", response_model=CompileResponse)
async def compile_endpoint(request: CompileRequest):
    return compile_code(request)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)