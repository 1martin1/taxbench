import subprocess
import tempfile
import os
import re
import time
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from typing import Optional

app = FastAPI()

class CompileRequest(BaseModel):
    fileName: str
    fileContent: str

REQUEST_LIMIT = 10
TIME_WINDOW = 60  # seconds
request_logs = {}

def check_rate_limit(ip: str):
    now = time.time()
    logs = request_logs.get(ip, [])
    logs = [t for t in logs if now - t < TIME_WINDOW]
    if len(logs) >= REQUEST_LIMIT:
        raise HTTPException(status_code=429, detail="Too many requests. Please try again later.")
    logs.append(now)
    request_logs[ip] = logs

@app.post("/compile", examples={
    "program1": {
        "summary": "Short C++ file",
        "value": {
            "fileName": "main.cpp",
            "fileContent": "void main(){ return 0; }"
        }
    },
    "program2": {
        "summary": "Short TypeScript file",
        "value": {
            "fileName": "background.ts",
            "fileContent": "let x = y * 2;"
        }
    }
})
async def compile_code(request: Request, request_body: CompileRequest):
    client_ip = request.client.host
    check_rate_limit(client_ip)
    
    file_name = request_body.fileName
    content = request_body.fileContent
    
    # Validate file name
    if len(file_name) > 255:
        return {"hasError": True, "compilerError": "File name too long"}
    if '/' in file_name or '\\' in file_name:
        return {"hasError": True, "compilerError": "Invalid file name: contains path separators"}
    if not re.match(r'^[\w\-. ]+\.(ts|cpp)$', file_name):
        return {"hasError": True, "compilerError": "Invalid file name format"}
    
    # Validate content size
    if len(content) > 10000:
        return {"hasError": True, "compilerError": "File content too long (max 10000 characters)"}
    
    # Determine file type
    ext = file_name.split(".")[-1].lower()
    if ext not in {"ts", "cpp"}:
        return {"hasError": True, "compilerError": f"Unsupported file extension: {ext}"}
    
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w+", delete=False) as temp_file:
            temp_file.write(content)
            temp_path = temp_file.name
        
        # Execute compiler
        if ext == "ts":
            command = ["tsc", temp_path, "--noEmit"]
        else:
            command = ["g++", temp_path, "-fsyntax-only"]
        
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=10
            )
        except FileNotFoundError:
            return {"hasError": True, "compilerError": "Compiler not found on the system"}
        except subprocess.TimeoutExpired:
            return {"hasError": True, "compilerError": "Compilation timed out"}
        
        has_error = result.returncode != 0
        error_output = result.stderr if has_error else None
        
        # Sanitize error output
        if error_output:
            error_output = error_output.replace(temp_path, file_name)
        
        return {
            "hasError": has_error,
            "compilerError": error_output
        }
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError as e:
                print(f"Error deleting temp file {temp_path}: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)