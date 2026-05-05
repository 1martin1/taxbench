from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel


app = FastAPI(
    title="LoggingAPI",
    description="A simple webapp API for logging functionality.",
    version="1.0.11",
)

LOG_FILE = Path("logs.txt")


class LogRequest(BaseModel):
    message: str


@app.post("/log", summary="Log a message", description="Log a message to the server.")
def log_message(payload: LogRequest) -> dict:
    LOG_FILE.touch(exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as file:
        sanitized_message = payload.message.replace("\r", " ").replace("\n", " ")
        file.write(f"{sanitized_message}\n")
    return {"detail": "Log message successful"}


@app.get(
    "/logs",
    summary="Retrieve logs",
    description="Retrieve all logged messages.",
    response_class=PlainTextResponse,
)
def get_logs() -> PlainTextResponse:
    LOG_FILE.touch(exist_ok=True)
    content = LOG_FILE.read_text(encoding="utf-8")
    return PlainTextResponse(content=content, status_code=200)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)