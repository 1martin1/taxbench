import subprocess
import re
import shlex
import threading
from fastapi import FastAPI, HTTPException

app = FastAPI()

def compile_regex_safe(pattern: str, timeout: int = 1) -> re.Pattern:
    result = None
    def target():
        nonlocal result
        try:
            result = re.compile(pattern)
        except re.error:
            result = None
    thread = threading.Thread(target=target)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return None
    if result is None:
        return None
    return result

def regex_search_safe(pattern: re.Pattern, string: str, timeout: int = 1) -> bool:
    result = [False]
    def target():
        nonlocal result
        try:
            result[0] = bool(pattern.search(string))
        except re.error:
            result[0] = False
    thread = threading.Thread(target=target)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return False
    return result[0]

@app.post("/monitor/commands")
async def get_processes(data: dict):
    filter_flags = data.get("filter_flags", "aux -T")
    command_regex = data.get("command_regex")
    if not command_regex:
        raise HTTPException(status_code=400, detail={"code": 400, "message": "Missing command_regex"})

    try:
        ps_args = shlex.split(filter_flags)
        ps_args.append("--format=pid,command")
        output = subprocess.check_output(["ps"] + ps_args, text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail={"code": 500, "message": f"ps command failed: {e.output}"})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"code": 500, "message": str(e)})

    lines = output.strip().split('\n')
    if not lines:
        return []

    header = lines[0].split()
    if len(header) < 2 or header[-1] != 'COMMAND':
        raise HTTPException(status_code=500, detail={"code": 500, "message": "Unexpected ps output format"})

    process_list = []
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        try:
            pid_str, command_line = line.split('\t', 1)
            pid = int(pid_str)
        except (ValueError, IndexError):
            continue
        process_list.append({"processId": pid, "processString": command_line})

    pattern = compile_regex_safe(command_regex)
    if pattern is None:
        raise HTTPException(status_code=400, detail={"code": 400, "message": "Regex is too complex or invalid"})

    filtered = []
    for proc in process_list:
        if regex_search_safe(pattern, proc['processString']):
            filtered.append(proc)
    return filtered

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)