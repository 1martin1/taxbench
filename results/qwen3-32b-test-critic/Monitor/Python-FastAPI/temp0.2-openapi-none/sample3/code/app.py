from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import re
import os

app = FastAPI()

class ProcessRequest(BaseModel):
    filter_flags: str = None
    command_regex: str

@app.post("/monitor/commands")
async def get_processes(request: ProcessRequest):
    command_regex = request.command_regex
    
    try:
        regex = re.compile(command_regex)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {e}")
    
    results = []
    proc_dir = '/proc'
    for entry in os.listdir(proc_dir):
        if entry.isdigit():
            pid = entry
            cmdline_path = os.path.join(proc_dir, pid, 'cmdline')
            try:
                with open(cmdline_path, 'r') as f:
                    cmdline = f.read().replace('\0', ' ').strip()
                if regex.search(cmdline):
                    results.append({
                        "processId": int(pid),
                        "processString": cmdline
                    })
            except (OSError, ValueError, UnicodeDecodeError):
                continue
    
    return results

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)