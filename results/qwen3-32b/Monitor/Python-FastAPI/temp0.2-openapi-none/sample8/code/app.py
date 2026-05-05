from fastapi import FastAPI, HTTPException
import subprocess
import re
import os

app = FastAPI()

@app.post("/monitor/commands")
async def get_processes(data: dict):
    filter_flags = data.get("filter_flags", "")
    command_regex = data.get("command_regex")
    
    if command_regex is None:
        raise HTTPException(status_code=400, detail={"code": 3, "message": "command_regex is required"})
    
    try:
        regex = re.compile(command_regex)
    except re.error as e:
        raise HTTPException(status_code=400, detail={"code": 2, "message": f"Invalid regular expression: {str(e)}"})
    
    ps_args = ["ps"] + filter_flags.split() + ["-o", "pid="]
    
    try:
        ps_output = subprocess.check_output(ps_args, stderr=subprocess.STDOUT, text=True)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail={"code": 1, "message": f"ps command failed: {e.output}"})
    
    pids = [line.strip() for line in ps_output.strip().split('\n') if line.strip().isdigit()]
    
    results = []
    for pid in pids:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline_bytes = f.read()
            cmdline = cmdline_bytes.decode('utf-8', errors='replace').replace('\0', ' ').strip()
            if regex.search(cmdline):
                results.append({"processId": int(pid), "processString": cmdline})
        except (FileNotFoundError, PermissionError, IsADirectoryError, UnicodeDecodeError):
            continue
    
    return results

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)