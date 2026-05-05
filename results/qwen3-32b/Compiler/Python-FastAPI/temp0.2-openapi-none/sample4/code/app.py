from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import subprocess
import os
import tempfile

app = FastAPI()

class CompileRequest(BaseModel):
    fileName: str
    fileContent: str

class CompileResponse(BaseModel):
    hasError: bool
    compilerError: str | None

@app.post("/compile", response_model=CompileResponse)
async def compile_code(request: CompileRequest):
    file_ext = os.path.splitext(request.fileName)[1].lower()
    if file_ext not in ('.ts', '.cpp', '.c++'):
        raise HTTPException(status_code=400, detail="Unsupported file type")
    
    compiler = 'tsc' if file_ext == '.ts' else 'g++'
    
    with tempfile.TemporaryDirectory() as temp_dir:
        file_path = os.path.join(temp_dir, request.fileName)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w') as f:
            f.write(request.fileContent)
        
        try:
            if compiler == 'tsc':
                result = subprocess.run(
                    ['tsc', request.fileName],
                    cwd=temp_dir,
                    capture_output=True,
                    text=True
                )
            else:
                result = subprocess.run(
                    ['g++', request.fileName, '-o', 'output'],
                    cwd=temp_dir,
                    capture_output=True,
                    text=True
                )
        except Exception as e:
            raise HTTPException(status_code=500, detail="Compiler execution failed")
        
        has_error = result.returncode != 0
        compiler_error = result.stderr if has_error else None
        
        return CompileResponse(hasError=has_error, compilerError=compiler_error)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)