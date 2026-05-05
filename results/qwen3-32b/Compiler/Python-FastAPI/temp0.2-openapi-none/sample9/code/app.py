from fastapi import FastAPI
from pydantic import BaseModel
import subprocess
import tempfile
import os

app = FastAPI()

class CompileRequest(BaseModel):
    fileName: str
    fileContent: str

@app.post("/compile")
async def compile_endpoint(request: CompileRequest):
    if not (request.fileName.endswith('.ts') or request.fileName.endswith('.cpp')):
        return {
            "hasError": True,
            "compilerError": "Unsupported file extension. Only .ts and .cpp are supported."
        }

    with tempfile.TemporaryDirectory() as temp_dir:
        base_file_name = os.path.basename(request.fileName)
        temp_file_path = os.path.join(temp_dir, base_file_name)
        
        with open(temp_file_path, 'w') as f:
            f.write(request.fileContent)
            
        if request.fileName.endswith('.ts'):
            command = ['tsc', temp_file_path]
        else:
            command = ['g++', '-fsyntax-only', temp_file_path]
            
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=temp_dir
        )
        
        has_error = result.returncode != 0
        compiler_error = None
        
        if has_error:
            error_output = (result.stderr.strip() or '') + (result.stdout.strip() or '')
            compiler_error = error_output if error_output else "Compilation failed with no output."
            
        return {
            "hasError": has_error,
            "compilerError": compiler_error if has_error else None
        }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)