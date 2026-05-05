import os
import re
import subprocess
import tempfile
from fastapi import FastAPI
from pydantic import BaseModel, Field, validator

app = FastAPI()

class CompileRequest(BaseModel):
    fileName: str
    fileContent: str = Field(..., max_length=1024*1024)  # 1MB limit

    @validator('fileName')
    def validate_file_name(cls, v):
        if not re.match(r'^[\w\-.]+\.(ts|cpp)$', v):
            raise ValueError("Invalid file name. Only alphanumeric, underscores, hyphens, and .ts or .cpp extensions allowed.")
        return v

    class Config:
        schema_extra = {
            "examples": [
                {
                    "summary": "Short C++ file",
                    "value": {
                        "fileName": "main.cpp",
                        "fileContent": "void main(){ return 0; }"
                    }
                },
                {
                    "summary": "Short TypeScript file",
                    "value": {
                        "fileName": "background.ts",
                        "fileContent": "let x = y * 2;"
                    }
                }
            ]
        }

@app.post("/compile")
async def compile_code(request: CompileRequest):
    safe_file_name = os.path.basename(request.fileName)
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = os.path.join(tmpdir, safe_file_name)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(request.fileContent)

        if request.fileName.endswith(".ts"):
            command = ["tsc", safe_file_name]
        else:
            command = ["g++", "-c", safe_file_name]

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                cwd=tmpdir,
                check=False,
                timeout=10
            )
        except subprocess.TimeoutExpired:
            return {
                "hasError": True,
                "compilerError": "Compilation timed out after 10 seconds."
            }

        if result.returncode != 0:
            error_output = (result.stdout + result.stderr).strip()
            return {
                "hasError": True,
                "compilerError": error_output if error_output else "Compilation failed without output."
            }
        else:
            return {
                "hasError": False,
                "compilerError": None
            }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)