import subprocess
import re
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

class ProcessRequest(BaseModel):
    filter_flags: str = None
    command_regex: str

@app.post("/monitor/commands")
async def get_processes(request: ProcessRequest):
    command = ["ps"]
    if request.filter_flags:
        command += request.filter_flags.split()
    
    try:
        output = subprocess.check_output(command, text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail={"code": 500, "message": f"Error running ps: {e.output}"})
    
    lines = output.splitlines()
    if not lines:
        return []
    
    data_lines = lines[1:]
    results = []
    
    try:
        regex = re.compile(request.command_regex)
    except re.error as e:
        raise HTTPException(status_code=400, detail={"code": 400, "message": "Invalid regular expression"})
    
    for line in data_lines:
        parts = line.split()
        if len(parts) < 11:
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        command_str = ' '.join(parts[10:])
        if regex.search(command_str):
            results.append({"processId": pid, "processString": command_str})
    
    return results

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)