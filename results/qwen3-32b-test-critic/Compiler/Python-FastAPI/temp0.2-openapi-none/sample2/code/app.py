from fastapi import FastAPI
from pydantic import BaseModel
import os
import subprocess
import tempfile
from pathlib import Path
import asyncio
import re

class CompileRequest(BaseModel):
    fileName: str
    fileContent: str

app = FastAPI()

def compile_code_sync(request: CompileRequest):
    # Validate file content for dangerous characters
    if re.search(r'[&|;`$]', request.fileContent):
        return {"hasError": True, "compilerError": "Invalid file content"}
    
    with tempfile.TemporaryDirectory() as temp_dir:
        file_path = os.path.join(temp_dir, request.fileName)
        temp_dir_path = Path(temp_dir)
        file_path_obj = Path(file_path)
        
        # Validate file name path traversal
        if not file_path_obj.is_relative_to(temp_dir_path):
            return {"hasError": True, "compilerError": "Invalid file name"}
            
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w') as f:
            f.write(request.fileContent)
            
        if request.fileName.endswith('.ts'):
            command = ['tsc', file_path]
        elif request.fileName.endswith('.cpp'):
            command = ['g++', '-fsyntax-only', file_path]
        else:
            return {"hasError": True, "compilerError": "Unsupported file type"}
            
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        output = result.stdout + result.stderr
        return {
            "hasError": result.returncode != 0,
            "compilerError": output if result.returncode != 0 else None
        }

@app.post("/compile")
async def compile_endpoint(request: CompileRequest):
    return await asyncio.to_thread(compile_code_sync, request)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)