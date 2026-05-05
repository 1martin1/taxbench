import os
import subprocess
import tempfile
from fastapi import FastAPI
from pydantic import BaseModel, field_validator

app = FastAPI()

class CompileRequest(BaseModel):
    fileName: str
    fileContent: str

    @field_validator('fileName')
    def validate_file_name(cls, value):
        # Check file extension (case-insensitive)
        if not (value.lower().endswith('.ts') or value.lower().endswith('.cpp')):
            raise ValueError('File must be TypeScript (.ts) or C++ (.cpp)')
        # Check for path components
        if os.path.basename(value) != value:
            raise ValueError('File name must not contain path components')
        return value

@app.post("/compile")
async def compile_code(request: CompileRequest):
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = os.path.join(tmpdir, request.fileName)
        with open(filepath, 'w') as f:
            f.write(request.fileContent)
        
        # Determine compiler based on file extension
        if request.fileName.lower().endswith('.ts'):
            compiler = 'tsc'
        else:
            compiler = 'g++'
        
        # Execute compiler
        result = subprocess.run(
            [compiler, request.fileName],
            cwd=tmpdir,
            capture_output=True,
            text=True
        )
        
        has_error = result.returncode != 0
        compiler_error = result.stderr if has_error else None
        
        return {
            "hasError": has_error,
            "compilerError": compiler_error
        }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)