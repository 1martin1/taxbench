from fastapi import FastAPI, Response
from pydantic import BaseModel, StringConstraints, field_validator
from typing_extensions import Annotated
import os

app = FastAPI()

class LogRequest(BaseModel):
    message: Annotated[str, StringConstraints(max_length=1024)]

    @field_validator("message")
    def sanitize_message(cls, v):
        return v.replace("\n", " ").replace("\r", " ")

@app.post("/log")
async def log_message(log: LogRequest):
    os.makedirs(os.path.dirname("logs.txt"), exist_ok=True)
    with open("logs.txt", "a") as f:
        f.write(log.message + "\n")
    return {"status": "success"}

@app.get("/logs")
async def get_logs():
    try:
        with open("logs.txt", "r") as f:
            content = f.read()
        return Response(content, media_type="text/plain")
    except FileNotFoundError:
        return Response(status_code=404, media_type="text/plain")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)