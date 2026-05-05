from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import subprocess
import re

app = FastAPI()

class ProcessRequest(BaseModel):
    filter_flags: Optional[str] = None
    command_regex: str

class ProcessResponseItem(BaseModel):
    processId: int
    processString: str

@app.post("/monitor/commands", response_model=List[ProcessResponseItem])
async def monitor_commands(request: ProcessRequest):
    # Split filter_flags into arguments
    ps_args = []
    if request.filter_flags:
        ps_args = request.filter_flags.split()

    # Run ps command
    try:
        result = subprocess.run(
            ['ps'] + ps_args,
            capture_output=True,
            text=True,
            check=True
        )
    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=400,
            detail=f"ps command failed: {e.stderr}"
        )

    # Split output into lines
    lines = result.stdout.splitlines()
    if not lines:
        return []

    # Skip header line
    process_lines = lines[1:]

    # Compile regex
    try:
        regex = re.compile(request.command_regex)
    except re.error as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid regex: {e}"
        )

    # Process each line
    results = []
    for line in process_lines:
        columns = line.split()
        if len(columns) < 2:
            continue  # Skip invalid lines
        pid_str = columns[1]
        try:
            pid = int(pid_str)
        except ValueError:
            continue  # Skip invalid PIDs

        # Read /proc/<pid>/cmdline
        cmdline_path = f"/proc/{pid}/cmdline"
        try:
            with open(cmdline_path, 'rb') as f:
                cmdline_bytes = f.read()
            # Split by null bytes and decode
            cmdline_parts = [
                part.decode('utf-8', errors='ignore')
                for part in cmdline_bytes.split(b'\0')
                if part
            ]
            cmdline = ' '.join(cmdline_parts)
        except (FileNotFoundError, PermissionError, IsADirectoryError, UnicodeDecodeError):
            # Skip inaccessible or terminated processes
            continue

        # Check regex match
        if regex.search(cmdline):
            results.append({
                "processId": pid,
                "processString": cmdline
            })

    return results

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)