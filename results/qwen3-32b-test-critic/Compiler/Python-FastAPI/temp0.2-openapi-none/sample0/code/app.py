import os
import subprocess
import tempfile
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class CompileRequest(BaseModel):
    fileName: str
    fileContent: str

@app.post("/compile")
async def compile_endpoint(request: CompileRequest):
    file_name = request.fileName
    file_content = request.fileContent

    with tempfile.TemporaryDirectory() as tmpdir:
        intended_path = os.path.join(tmpdir, file_name)
        resolved_tmpdir = os.path.realpath(tmpdir)
        resolved_intended = os.path.realpath(intended_path)
        
        # Check for path traversal attempts
        if not resolved_intended.startswith(resolved_tmpdir + os.path.sep):
            return {
                "hasError": True,
                "compilerError": "Invalid file name (path traversal attempt)"
            }

        os.makedirs(os.path.dirname(intended_path), exist_ok=True)
        with open(intended_path, "w") as f:
            f.write(file_content)
        
        if file_name.endswith(".ts"):
            cmd = ["tsc", file_name]
        elif file_name.endswith(".cpp"):
            cmd = ["g++", file_name, "-o", "output"]
        else:
            return {"hasError": True, "compilerError": "Unsupported file type"}
        
        result = subprocess.run(
            cmd,
            cwd=tmpdir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        has_error = result.returncode != 0
        error_output = result.stderr

        return {
            "hasError": has_error,
            "compilerError": error_output if has_error else None
        }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "5000")))