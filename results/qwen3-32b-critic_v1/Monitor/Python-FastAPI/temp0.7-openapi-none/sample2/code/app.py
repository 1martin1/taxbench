import subprocess
import re
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, TimeoutError

app = FastAPI()

class ProcessFilter(BaseModel):
    filter_flags: Optional[str] = "aux"
    command_regex: str

@app.post("/monitor/commands")
async def get_matching_processes(filter_data: ProcessFilter):
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(re.compile, filter_data.command_regex)
            try:
                regex = future.result(timeout=1)
            except re.error as e:
                raise HTTPException(status_code=400, detail={"code": 400, "message": f"Invalid regex: {str(e)}"})
            except TimeoutError:
                raise HTTPException(status_code=400, detail={"code": 400, "message": "Regex compilation timed out"})
    except Exception:
        raise HTTPException(status_code=500, detail={"code": 500, "message": "Unexpected error during regex compilation"})

    ps_args = ["ps"] + filter_data.filter_flags.split()
    
    try:
        result = subprocess.run(
            ps_args, 
            capture_output=True, 
            encoding='utf-8', 
            errors='replace', 
            check=True
        )
        output_lines = result.stdout.splitlines()
    except subprocess.CalledProcessError:
        raise HTTPException(status_code=500, detail={"code": 500, "message": "Failed to execute ps command"})
    
    if not output_lines:
        return []
    
    headers = output_lines[0].split()
    try:
        pid_index = headers.index("PID")
    except ValueError:
        raise HTTPException(status_code=500, detail={"code": 500, "message": "ps output does not contain PID column"})
    
    processes = []
    for line in output_lines[1:]:
        columns = line.split()
        if len(columns) <= pid_index:
            continue
        try:
            pid = int(columns[pid_index])
            if regex.search(line):
                processes.append({"processId": pid, "processString": line})
        except (ValueError, IndexError):
            continue
    
    return processes

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)