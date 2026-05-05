import os
from fastapi import FastAPI, HTTPException
from fastapi import Body
from fastapi.responses import Response
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


def ensure_log_file():
    """Make sure the log file exists."""
    if not os.path.isfile(LOG_FILE):
        # Create an empty file
        open(LOG_FILE, "a", encoding="utf-8").close()


@app.post("/log", summary="Log a message", description="Log a message to the server.")
async def log_message(payload: LogMessage = Body(...)):
    """
    Append the provided message to the log file, one line per message.
    """
    ensure_log_file()
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(payload.message.rstrip("\n") + "\n")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to write log: {exc}")
    return {"detail": "Log message successful"}


@app.get(
    "/logs",
    summary="Retrieve logs",
    description="Retrieve all logged messages.",
    responses={200: {"content": {"text/plain": {}}}},
)
async def get_logs():
    """
    Return the contents of the log file as plain text.
    """
    ensure_log_file()
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to read logs: {exc}")
    return Response(content=content, media_type="text/plain")


if __name__ == "__main__":
    # Run the application on 0.0.0.0:5000
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)