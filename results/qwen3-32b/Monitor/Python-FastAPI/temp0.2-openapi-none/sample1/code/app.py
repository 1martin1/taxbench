import subprocess
import re
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List

app = FastAPI()

class ProcessRequest(BaseModel):
    filter_flags: Optional[str] = "aux"
    command_regex: str

@app.post("/monitor/commands")
async def get_processes(request: ProcessRequest):
    try:
        pattern = re.compile(request.command_regex)
    except re.error as e:
        raise HTTPException(status_code=400, detail={"code": 400, "message": f"Invalid regex: {str(e)}"})

    ps_cmd = ["ps"]
    if request.filter_flags:
        ps_cmd += request.filter_flags.split()

    try:
        result = subprocess.run(ps_cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail={"code": 500, "message": f"ps command failed: {e.stderr}"})

    lines = result.stdout.splitlines()
    if not lines:
        return []

    header = lines[0]
    process_lines = lines[1:]

    header_parts = header.split()
    command_col_index = -1
    for i, col in enumerate(header_parts):
        if col.lower() in ['command', 'args', 'cmd']:
            command_col_index = i
            break

    if command_col_index == -1:
        raise HTTPException(status_code=500, detail={"code": 500, "message": "Command column not found in ps output"})

    matches = []
    for line in process_lines:
        parts = line.split()
        if len(parts) <= command_col_index:
            continue
        if len(parts) <= 1:
            continue
        pid_str = parts[1]
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        command = ' '.join(parts[command_col_index:])
        if pattern.search(command):
            matches.append({"processId": pid, "processString": command})

    return matches

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)