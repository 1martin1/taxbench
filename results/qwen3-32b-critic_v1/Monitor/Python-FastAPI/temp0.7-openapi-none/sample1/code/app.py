import sys
import os
import subprocess
import shlex
import re
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List

app = FastAPI()

class ProcessRequest(BaseModel):
    filter_flags: Optional[str] = None
    command_regex: str

class ProcessResponse(BaseModel):
    processId: int
    processString: str

@app.post("/monitor/commands", response_model=List[ProcessResponse])
async def get_processes(request: ProcessRequest):
    # Check if the system is Linux
    if sys.platform != 'linux':
        raise HTTPException(status_code=500, detail={"code": 500, "message": "Unsupported operating system"})
    
    # Validate and compile the regex to prevent ReDoS
    dangerous_patterns = [
        r'$$(.*?)+',  # Nested quantifiers
        r'(?:.*?){',   # Potentially complex repetition
        r'(?R)',       # Recursion
        r'(?P<',       # Named groups with backreferences
        r'$$(?:a+|b+)+', # Example of exponential backtracking
    ]
    for pattern in dangerous_patterns:
        if re.search(pattern, request.command_regex):
            raise HTTPException(status_code=400, detail={"code": 400, "message": "Regex contains potentially dangerous patterns"})
    
    # Compile the regex
    try:
        regex = re.compile(request.command_regex)
    except re.error:
        raise HTTPException(status_code=400, detail={"code": 400, "message": "Invalid regular expression"})
    
    # Construct the ps command
    if request.filter_flags:
        ps_args = shlex.split(request.filter_flags)
        command = ["ps", "-o", "pid=", "--no-headers"] + ps_args
    else:
        # Default to all processes
        command = ["ps", "-e", "-o", "pid="]
    
    try:
        # Run the command with a timeout
        result = subprocess.run(command, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            error_msg = result.stderr.strip() or "ps command failed"
            raise HTTPException(status_code=400, detail={"code": 400, "message": error_msg})
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail={"code": 400, "message": "ps command not found"})
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=400, detail={"code": 400, "message": f"Error running ps: {e}"})
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail={"code": 408, "message": "ps command timed out"})
    
    # Parse the output to get PIDs
    pids = []
    lines = result.stdout.splitlines()
    for line in lines:
        try:
            pid = int(line.strip())
            pids.append(pid)
        except ValueError:
            continue
    
    # Limit the number of PIDs to prevent excessive resource usage
    if len(pids) > 1000:
        raise HTTPException(status_code=413, detail={"code": 413, "message": "Too many processes to process"})
    
    # Now, for each PID, read cmdline from /proc
    result_processes = []
    for pid in pids:
        cmdline_path = os.path.join('/proc', str(pid), 'cmdline')
        try:
            with open(cmdline_path, 'rb') as f:
                cmdline = f.read().decode('utf-8', errors='ignore')
                args = cmdline.split('\x00')
                command_str = ' '.join(args).strip('\x00')
                if regex.search(command_str):
                    result_processes.append({
                        "processId": pid,
                        "processString": command_str
                    })
        except (OSError, UnicodeDecodeError):
            continue
    
    return result_processes

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)