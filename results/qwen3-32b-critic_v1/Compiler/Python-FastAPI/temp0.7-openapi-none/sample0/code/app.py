from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import subprocess
import os
import tempfile

app = FastAPI()

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_COMPILE_TIME = 10  # seconds

class CompileRequest(BaseModel):
    fileName: str
    fileContent: str

@app.post("/compile")
def compile_code(request: CompileRequest):
    # Validate file size
    if len(request.fileContent) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File content too large")
    
    # Sanitize file name (prevent path traversal)
    if os.path.basename(request.fileName) != request.fileName:
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    ext = os.path.splitext(request.fileName)[1]
    if ext not in [".ts", ".cpp"]:
        raise HTTPException(status_code=400, detail="Unsupported file type")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = os.path.join(tmpdir, request.fileName)
        
        # Write file content to temporary directory
        with open(filepath, 'w') as f:
            f.write(request.fileContent)
        
        # Determine compiler command
        if ext == ".ts":
            command = ['tsc', filepath]
        else:
            output_path = os.path.join(tmpdir, 'output')
            command = ['g++', '-std=c++17', '-x', 'c++', filepath, '-o', output_path]
        
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=MAX_COMPILE_TIME
            )
        except subprocess.TimeoutExpired:
            return {"hasError": True, "compilerError": "Compilation timed out"}
        except FileNotFoundError:
            return {"hasError": True, "compilerError": "Required compiler not found. Please ensure TypeScript and g++ are installed."}
        except Exception as e:
            return {"hasError": True, "compilerError": f"An error occurred: {str(e)}"}
        
        has_error = result.returncode != 0
        compiler_error = result.stderr if has_error else None
        
        return {"hasError": has_error, "compilerError": compiler_error}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)