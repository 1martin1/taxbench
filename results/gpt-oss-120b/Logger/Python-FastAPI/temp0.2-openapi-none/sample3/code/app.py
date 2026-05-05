from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
from pydantic import BaseModel, Field
from pathlib import Path
import uvicorn

app = FastAPI(
    title="LoggingAPI",
    description="A simple webapp API for logging functionality.",
    version="1.0.11",
)

LOG_FILE = Path("logs.txt")


class LogMessage(BaseModel):
    message: str = Field(..., description="Message to be logged")


def _ensure_log_file():
    """Make sure the log file exists."""
    if not LOG_FILE.exists():
        LOG_FILE.touch()


@app.post("/log", summary="Log a message", description="Log a message to the server.")
async def log_message(payload: LogMessage):
    """
    Append the received message to the log file, one line per message.
    """
    try:
        _ensure_log_file()
        # Write the message followed by a newline
        LOG_FILE.write_text(
            LOG_FILE.read_text(encoding="utf-8") + payload.message + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return JSONResponse(content={"status": "ok"})


@app.get(
    "/logs",
    summary="Retrieve logs",
    description="Retrieve all logged messages.",
    response_class=PlainTextResponse,
)
async def get_logs():
    """
    Return the entire content of the log file as plain text.
    """
    _ensure_log_file()
    try:
        content = LOG_FILE.read_text(encoding="utf-8")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return PlainTextResponse(content=content, media_type="text/plain")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)