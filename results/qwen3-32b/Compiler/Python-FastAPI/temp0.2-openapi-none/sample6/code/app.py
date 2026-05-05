from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional
import subprocess
import os
import tempfile

app = FastAPI()

class CompileRequest(BaseModel):
    fileName: str
    fileContent: str

class CompileResponse(BaseModel):
    hasError: bool
    compilerError: Optional[str]

@app.post("/compile", response_model=CompileResponse)
async def compile_code(request: CompileRequest):
    base_name = os.path.basename(request.fileName)
    if not (base_name.endswith('.ts') or base_name.endswith('.cpp')):
        return CompileResponse(hasError=True, compilerError="Unsupported file type")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        file_path = os.path.join(temp_dir, base_name)
        with open(file_path, 'w') as f:
            f.write(request.fileContent)
        
        if base_name.endswith('.ts'):
            command = ['tsc', '--noEmit', base_name]
        else:  # .cpp
            command = ['g++', '-fsyntax-only', base_name]
        
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=temp_dir,
            text=True
        )
        
        has_error = result.returncode != 0
        error_output = result.stderr or result.stdout
        
        return CompileResponse(hasError=has_error, compilerError=error_output if has_error else None)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)