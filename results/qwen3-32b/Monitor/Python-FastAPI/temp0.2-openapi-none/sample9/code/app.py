import subprocess
import re
import shlex
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

class ProcessRequest(BaseModel):
    filter_flags: str = ""
    command_regex: str

@app.post("/monitor/commands")
async def get_processes(request: ProcessRequest):
    try:
        filter_parts = shlex.split(request.filter_flags)
        result = subprocess.run(
            ['ps'] + filter_parts,
            capture_output=True,
            text=True,
            check=True
        )
        output = result.stdout
        lines = output.splitlines()
        if not lines:
            return []
        header = lines[0].split()
        try:
            pid_index = header.index('PID')
        except ValueError:
            raise HTTPException(status_code=500, detail="PID column not found in ps output")
        try:
            command_index = header.index('COMMAND')
        except ValueError:
            try:
                command_index = header.index('CMD')
            except ValueError:
                raise HTTPException(status_code=500, detail="Command column not found in ps output")
        process_list = []
        regex = re.compile(request.command_regex)
        for line in lines[1:]:
            parts = line.split()
            if len(parts) <= max(pid_index, command_index):
                continue
            pid = parts[pid_index]
            command = parts[command_index]
            if regex.search(command):
                process_list.append({
                    "processId": int(pid),
                    "processString": command
                })
        return process_list
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"Error running ps: {e.stderr}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)