from fastapi import FastAPI, HTTPException, status
import subprocess
import shlex
import re
import uvicorn
from pydantic import BaseModel

app = FastAPI()

class ProcessRequest(BaseModel):
    command_regex: str
    filter_flags: str = "aux"

@app.post("/monitor/commands")
async def get_processes(data: ProcessRequest):
    filter_flags = data.filter_flags
    command_regex = data.command_regex

    try:
        pattern = re.compile(command_regex)
    except re.error as e:
        raise HTTPException(status_code=400, detail={"code": 5, "message": f"Invalid regular expression: {e}"})

    try:
        ps_args = ["ps"] + shlex.split(filter_flags)
        result = subprocess.run(ps_args, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail={"code": 1, "message": f"ps command failed: {e}"})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"code": 2, "message": f"Error executing ps: {e}"})

    lines = result.stdout.splitlines()
    if not lines:
        return []

    header = lines[0].split()
    try:
        pid_index = header.index("PID")
    except ValueError:
        raise HTTPException(status_code=500, detail={"code": 3, "message": "PID column not found in ps output"})

    try:
        if "CMD" in header:
            cmd_index = header.index("CMD")
        elif "COMMAND" in header:
            cmd_index = header.index("COMMAND")
        else:
            raise HTTPException(status_code=500, detail={"code": 4, "message": "Command column not found in ps output"})
    except ValueError:
        raise HTTPException(status_code=500, detail={"code": 4, "message": "Command column not found in ps output"})

    processes = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) <= max(pid_index, cmd_index):
            continue
        try:
            pid = int(parts[pid_index])
        except ValueError:
            continue
        command = parts[cmd_index]
        if pattern.search(command):
            processes.append({"processId": pid, "processString": line})

    return processes

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=12350)