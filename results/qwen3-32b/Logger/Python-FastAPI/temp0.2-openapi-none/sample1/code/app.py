import os
from fastapi import FastAPI, Response, HTTPException
from pydantic import BaseModel

app = FastAPI()

class LogEntry(BaseModel):
    message: str

@app.post("/log")
def create_log(entry: LogEntry):
    try:
        with open("logs.txt", "a") as f:
            f.write(entry.message + "\n")
        return Response(status_code=200)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/logs")
def read_logs():
    try:
        if not os.path.exists("logs.txt"):
            return ""
        with open("logs.txt", "r") as f:
            return f.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)