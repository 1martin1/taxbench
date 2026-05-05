from fastapi import FastAPI, Response
from pydantic import BaseModel
import os

app = FastAPI()

class LogEntry(BaseModel):
    message: str

LOG_FILE = "logs.txt"

@app.post("/log")
async def log_message(entry: LogEntry):
    with open(LOG_FILE, "a") as f:
        f.write(entry.message + "\n")
    return {"status": "success"}

@app.get("/logs")
async def get_logs():
    if not os.path.exists(LOG_FILE):
        return Response("", media_type="text/plain")
    with open(LOG_FILE, "r") as f:
        content = f.read()
    return Response(content, media_type="text/plain")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)