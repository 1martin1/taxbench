import os
import subprocess
import tempfile
from fastapi import FastAPI, Request, HTTPException
import uvicorn

app = FastAPI()

@app.post("/compile")
async def compile_code(request: Request):
    data = await request.json()
    file_name = data.get("fileName")
    file_content = data.get("fileContent")

    if not file_name or not file_content:
        raise HTTPException(status_code=400, detail="Missing fileName or fileContent")

    if not (file_name.endswith(".ts") or file_name.endswith(".cpp")):
        raise HTTPException(status_code=400, detail="Unsupported file type. Use .ts or .cpp")

    with tempfile.TemporaryDirectory() as temp_dir:
        file_path = os.path.join(temp_dir, file_name)
        with open(file_path, 'w') as f:
            f.write(file_content)

        if file_name.endswith(".ts"):
            result = subprocess.run(
                ["tsc", file_name, "--noEmit"],
                cwd=temp_dir,
                capture_output=True,
                text=True
            )
        else:  # .cpp
            result = subprocess.run(
                ["g++", file_name, "-o", "/dev/null"],
                cwd=temp_dir,
                capture_output=True,
                text=True
            )

        has_error = result.returncode != 0
        error_output = result.stderr.strip() if has_error else None

        return {
            "hasError": has_error,
            "compilerError": error_output
        }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)