import os
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
import uvicorn

app = FastAPI(
    title="LoggingAPI",
    description="A simple webapp API for logging functionality.",
    version="1.0.11",
)

LOG_FILE = "logs.txt"


class LogMessage(BaseModel):
    message: str


def append_log(message: str) -> None:
    """Append a single log line to the log file."""
    # Ensure the directory exists (not needed here but safe)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(message.rstrip("\n") + "\n")


def read_logs() -> str:
    """Read the entire log file, returning its content as a string."""
    if not os.path.exists(LOG_FILE):
        return ""
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        return f.read()


@app.post(
    "/log",
    summary="Log a message",
    description="Log a message to the server.",
    responses={200: {"description": "Log message successful"}},
)
def log_message(payload: LogMessage):
    try:
        append_log(payload.message)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"detail": "Log message successful"}


@app.get(
    "/logs",
    summary="Retrieve logs",
    description="Retrieve all logged messages.",
    responses={
        200: {
            "description": "Logs retrieved successfully",
            "content": {"text/plain": {"schema": {"type": "string"}}},
        }
    },
    response_class=Response,
)
def get_logs():
    content = read_logs()
    return Response(content=content, media_type="text/plain")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)