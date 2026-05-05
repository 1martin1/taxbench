from fastapi import FastAPI, Response
from pydantic import BaseModel
import os

app = FastAPI()

class LogMessage(BaseModel):
    message: str

@app.post("/log")
async def log_message(data: LogMessage):
    message = data.message
    with open("logs.txt", "a") as f:
        f.write(message + "\n")
    return Response(status_code=200)

@app.get("/logs")
async def get_logs():
    if not os.path.exists("logs.txt"):
        return Response(content="", media_type="text/plain")
    with open("logs.txt", "r") as f:
        content = f.read()
    return Response(content=content, media_type="text/plain")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)