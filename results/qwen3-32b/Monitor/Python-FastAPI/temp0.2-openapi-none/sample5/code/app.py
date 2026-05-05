from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import subprocess
import re
import shlex

app = FastAPI()

class ProcessRequest(BaseModel):
    filter_flags: str = "aux"
    command_regex: str

@app.post("/monitor/commands")
async def get_processes(request: ProcessRequest):
    filter_flags = request.filter_flags
    command_regex = request.command_regex

    # Validate filter_flags against allowed patterns
    allowed_flags = {"aux", "ef", "a", "x", "u", "f", "e"}
    if not all(c in allowed_flags for c in filter_flags.replace(" ", "")):
        raise HTTPException(
            status_code=400,
            detail={"code": 4, "message": "Invalid filter flags"}
        )

    try:
        result = subprocess.run(
            ["ps"] + shlex.split(filter_flags),
            capture_output=True,
            text=True,
            check=True
        )
    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=400,
            detail={"code": 1, "message": "Failed to execute ps command"}
        )

    lines = result.stdout.splitlines()
    if not lines:
        return []

    header = lines[0].strip().split()
    try:
        pid_index = header.index('PID')
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"code": 2, "message": "PID column not found in ps output"}
        )

    try:
        regex = re.compile(command_regex)
    except re.error as e:
        raise HTTPException(
            status_code=400,
            detail={"code": 3, "message": "Invalid regular expression"}
        )

    processes = []
    for line in lines[1:]:
        columns = line.strip().split()
        if len(columns) <= pid_index:
            continue
        try:
            pid = int(columns[pid_index])
        except ValueError:
            continue
        process_string = line.strip()
        if regex.search(process_string):
            processes.append({"processId": pid, "processString": process_string})

    return processes

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)