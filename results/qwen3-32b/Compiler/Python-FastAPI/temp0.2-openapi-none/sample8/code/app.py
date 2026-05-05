from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional
import os
import subprocess
import tempfile

app = FastAPI(title="CompilationNow", version="1.0.0")

class CompileRequest(BaseModel):
    fileName: str
    fileContent: str

class CompileResponse(BaseModel):
    hasError: bool
    compilerError: Optional[str]

def compile_code(file_name: str, content: str) -> tuple[bool, Optional[str]]:
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, file_name)
            with open(file_path, 'w') as f:
                f.write(content)
            if file_name.endswith('.ts'):
                subprocess.run(['tsc', file_path], capture_output=True, text=True, check=True)
            elif file_name.endswith('.cpp'):
                subprocess.run(['g++', '-c', file_path], capture_output=True, text=True, check=True)
            else:
                return (False, f"Unsupported file extension: {file_name}")
            return (False, None)
    except subprocess.CalledProcessError as e:
        return (True, e.stderr or e.stdout)
    except FileNotFoundError as e:
        return (True, f"Compiler not found: {e.filename}")
    except Exception as e:
        return (True, str(e))

@app.post("/compile", response_model=CompileResponse)
async def compile_endpoint(request: CompileRequest):
    has_error, error_msg = compile_code(request.fileName, request.fileContent)
    return {"hasError": has_error, "compilerError": error_msg}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)