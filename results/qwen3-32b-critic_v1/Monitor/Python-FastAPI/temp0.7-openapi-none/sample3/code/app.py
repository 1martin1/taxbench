import subprocess
import re
import shlex
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
import threading

app = FastAPI()

class ProcessRequest(BaseModel):
    filter_flags: Optional[str] = None
    command_regex: str

class ProcessResponse(BaseModel):
    processId: int
    processString: str

MAX_REGEX_LENGTH = 100
PS_CMD_TIMEOUT = 10
REGEX_SEARCH_TIMEOUT = 5

@app.post("/monitor/commands", response_model=List[ProcessResponse])
async def get_processes(request: ProcessRequest):
    # Validate regex length to prevent resource exhaustion
    if len(request.command_regex) > MAX_REGEX_LENGTH:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Regex is too long")
    
    # Compile regex with error handling
    try:
        regex = re.compile(request.command_regex)
    except re.error as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid regex: {e}")
    
    # Build base command
    cmd = ["ps", "--no-headers", "--format", "pid,args"]
    
    # Process filter_flags securely
    if request.filter_flags:
        try:
            flags_args = shlex.split(request.filter_flags)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid filter flags: {e}")
        
        # Validate filter flags to prevent command injection
        disallowed_args = {"--no-headers", "--format"}
        for arg in flags_args:
            if arg in disallowed_args or arg.startswith("--format="):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Filter flags cannot contain {arg}")
        
        cmd += flags_args
    
    # Execute command with timeout
    try:
        result = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True,
            timeout=PS_CMD_TIMEOUT
        )
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() or "ps command failed"
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error_msg)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="ps command timed out")
    
    if result.returncode != 0:
        error_msg = result.stderr.strip() or "ps command failed"
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error_msg)
    
    processes = []
    for line in result.stdout.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        command = parts[1]
        
        # Execute regex search with timeout
        match_found = [False]
        def regex_search():
            match_found[0] = regex.search(command) is not None
        
        thread = threading.Thread(target=regex_search)
        thread.start()
        thread.join(REGEX_SEARCH_TIMEOUT)
        
        if thread.is_alive():
            raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Regex search timed out")
        
        if match_found[0]:
            processes.append(ProcessResponse(processId=pid, processString=command))
    
    return processes

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)