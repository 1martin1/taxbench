import subprocess
import re
import shlex
from fastapi import FastAPI, HTTPException

app = FastAPI()

@app.post("/monitor/commands")
async def get_processes(data: dict):
    filter_flags = data.get("filter_flags", "aux -T")
    command_regex = data.get("command_regex")
    
    if not command_regex:
        raise HTTPException(status_code=400, detail={"code": 400, "message": "command_regex is required"})
    
    try:
        ps_args = shlex.split(filter_flags)
        ps_args += ['-o', 'pid,command']
        output = subprocess.check_output(['ps'] + ps_args, text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail={"code": 500, "message": f"ps command failed: {e.output}"})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"code": 500, "message": str(e)})
    
    lines = output.strip().split('\n')
    if not lines:
        return []
    
    header = lines[0]
    if 'PID' not in header or 'COMMAND' not in header:
        raise HTTPException(status_code=500, detail={"code": 500, "message": "Unexpected ps output format"})
    
    try:
        regex = re.compile(command_regex)
    except re.error as e:
        raise HTTPException(status_code=400, detail={"code": 400, "message": f"Invalid regex: {e}"})
    
    result = []
    for line in lines[1:]:
        parts = line.split(maxsplit=1)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        command = parts[1]
        if regex.search(command):
            result.append({"processId": pid, "processString": command})
    
    return result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)