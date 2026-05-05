from fastapi import FastAPI, HTTPException
import subprocess
import re
from pydantic import BaseModel
from typing import List, Optional

app = FastAPI()

class ProcessInfo(BaseModel):
    processId: int
    processString: str

class Error(BaseModel):
    code: int
    message: str

@app.post("/monitor/commands", response_model=List[ProcessInfo], responses={400: {"model": Error}})
async def get_processes(data: dict):
    filter_flags = data.get("filter_flags", "aux")
    command_regex = data.get("command_regex")
    
    if not command_regex:
        raise HTTPException(status_code=400, detail=Error(code=1, message="command_regex is required"))
    
    try:
        command = f"ps {filter_flags}"
        result = subprocess.run(command, shell=True, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=400, detail=Error(code=2, message=f"ps command failed: {e.stderr}"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=Error(code=3, message=f"Error running ps: {str(e)}"))
    
    output = result.stdout
    lines = output.splitlines()
    
    if not lines:
        return []
    
    header = lines[0].split()
    try:
        pid_index = header.index("PID")
    except ValueError:
        raise HTTPException(status_code=400, detail=Error(code=4, message="Header does not contain PID column"))
    try:
        command_index = header.index("COMMAND")
    except ValueError:
        try:
            command_index = header.index("CMD")
        except ValueError:
            raise HTTPException(status_code=400, detail=Error(code=5, message="Header does not contain COMMAND or CMD column"))
    
    try:
        regex = re.compile(command_regex)
    except re.error as e:
        raise HTTPException(status_code=400, detail=Error(code=6, message=f"Invalid regex: {e}"))
    
    processes = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) <= max(pid_index, command_index):
            continue
        try:
            pid = int(parts[pid_index])
        except (ValueError, IndexError):
            continue
        command = ' '.join(parts[command_index:])
        if regex.search(command):
            processes.append({
                "processId": pid,
                "processString": command
            })
    
    return processes

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)