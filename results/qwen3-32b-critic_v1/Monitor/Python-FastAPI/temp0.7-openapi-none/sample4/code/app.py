import subprocess
import re
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional

app = FastAPI()

# Security and performance limits
MAX_FILTER_FLAGS_LEN = 1024
MAX_FILTER_ARGS = 10
MAX_PIDS_TO_PROCESS = 1000
REGEX_COMPILE_TIMEOUT = 1  # seconds

class ProcessRequest(BaseModel):
    filter_flags: Optional[str] = "aux -T"
    command_regex: str

@app.post("/monitor/commands")
async def monitor_commands(request: ProcessRequest):
    filter_flags = request.filter_flags
    command_regex_str = request.command_regex

    # Validate filter_flags length
    if len(filter_flags) > MAX_FILTER_FLAGS_LEN:
        raise HTTPException(status_code=400, detail={"code": 2, "message": "filter_flags too long"})

    # Split into arguments and validate count
    ps_args = ["ps"] + filter_flags.split()
    if len(ps_args) - 1 > MAX_FILTER_ARGS:  # Subtract 1 for "ps" itself
        raise HTTPException(status_code=400, detail={"code": 3, "message": "Too many filter flags"})

    try:
        ps_output = subprocess.check_output(ps_args, text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=400, detail={"code": 5, "message": f"Invalid ps flags: {e.output.strip()}"})
    except Exception as e:
        raise HTTPException(status_code=400, detail={"code": 5, "message": f"ps command failed: {str(e)}"})

    lines = ps_output.strip().split('\n')
    if lines and lines[0].lower().startswith('user '):
        lines = lines[1:]

    pids = []
    for line in lines:
        parts = line.split()
        if len(parts) >= 2:
            try:
                pid = int(parts[1])
                pids.append(pid)
            except ValueError:
                continue

    if len(pids) > MAX_PIDS_TO_PROCESS:
        raise HTTPException(status_code=400, detail={"code": 6, "message": "Too many PIDs to process"})

    try:
        command_regex = re.compile(command_regex_str, timeout=REGEX_COMPILE_TIMEOUT)
    except re.error as e:
        raise HTTPException(status_code=400, detail={"code": 4, "message": f"Invalid regex: {e}"})

    results = []
    pid_count = 0
    for pid in pids:
        if pid_count >= MAX_PIDS_TO_PROCESS:
            break
        cmdline_path = f"/proc/{pid}/cmdline"
        try:
            with open(cmdline_path, 'r') as f:
                cmdline = f.read().replace('\0', ' ').strip()
            if command_regex.search(cmdline):
                results.append({"processId": pid, "processString": cmdline})
            pid_count += 1
        except (FileNotFoundError, PermissionError, IsADirectoryError, OSError):
            continue

    return results

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)