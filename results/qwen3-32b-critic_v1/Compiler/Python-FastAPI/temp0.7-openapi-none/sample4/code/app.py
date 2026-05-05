from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
import subprocess
import os
import tempfile

app = FastAPI()

class CompileRequest(BaseModel):
    fileName: str
    fileContent: str = Field(..., max_length=1024 * 1024)  # 1MB limit

class CompileResponse(BaseModel):
    hasError: bool
    compilerError: Optional[str]

@app.post("/compile", response_model=CompileResponse)
async def compile_code(request: CompileRequest):
    safe_name = os.path.basename(request.fileName)
    ext = os.path.splitext(safe_name)[1].lower()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = os.path.join(temp_dir, safe_name)
        with open(temp_path, 'w') as f:
            f.write(request.fileContent)
        
        if ext == '.cpp':
            command = ['g++', '-o', '/dev/null', temp_path]
        elif ext == '.ts':
            command = ['tsc', temp_path]
        else:
            raise HTTPException(status_code=400, detail="Unsupported file type. Use .cpp or .ts")
        
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=500,
                detail=f"Compiler for {ext} not found. Please install required compiler."
            )
        
        try:
            stdout, stderr = process.communicate(timeout=10)
            returncode = process.returncode
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            returncode = -1
            stderr += "\nCompilation timed out after 10 seconds."
        
        has_error = returncode != 0
        error_message = stderr if has_error else None
        
        return CompileResponse(hasError=has_error, compilerError=error_message)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)