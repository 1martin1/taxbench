import subprocess
import tempfile
import os
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
        file_path = os.path.join(tmpdir, file_name)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w") as f:
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
    uvicorn.run(app, host="0.0.0.0", port=5000)