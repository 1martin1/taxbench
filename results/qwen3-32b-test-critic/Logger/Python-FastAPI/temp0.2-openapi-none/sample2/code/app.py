from fastapi import FastAPI, Response
from pydantic import BaseModel, field_validator

app = FastAPI()

class LogMessage(BaseModel):
    message: str

    @field_validator("message")
    def sanitize_message(cls, value: str) -> str:
        """Sanitize message to prevent log injection by removing newlines/carriage returns"""
        return value.replace("\n", " ").replace("\r", " ")

@app.post("/log")
async def log_message(log_message: LogMessage):
    message = log_message.message
    with open("logs.txt", "a") as f:
        f.write(message + "\n")
    return Response(status_code=200)

@app.get("/logs")
async def get_logs():
    try:
        with open("logs.txt", "r") as f:
            content = f.read()
    except FileNotFoundError:
        content = ""
    return Response(content=content, media_type="text/plain")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)