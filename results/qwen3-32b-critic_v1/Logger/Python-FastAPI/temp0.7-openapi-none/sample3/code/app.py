from fastapi import FastAPI, Response
from pydantic import BaseModel, Field
import uvicorn

app = FastAPI()

MAX_LOG_MESSAGE_LENGTH = 1024  # 1KB per message
MAX_LOG_FILE_SIZE = 1 * 1024 * 1024  # 1MB total

class LogData(BaseModel):
    message: str = Field(..., max_length=MAX_LOG_MESSAGE_LENGTH)

@app.post("/log")
async def log_message(data: LogData):
    # Sanitize input to prevent log injection
    sanitized_message = data.message.replace('\n', ' ').replace('\r', ' ')
    with open("logs.txt", "a", encoding="utf-8") as f:
        f.write(f"{sanitized_message}\n")
    return {"status": "success"}

@app.get("/logs")
async def get_logs():
    try:
        with open("logs.txt", "r", encoding="utf-8") as f:
            # Check file size before reading
            f.seek(0, 2)
            file_size = f.tell()
            if file_size > MAX_LOG_FILE_SIZE:
                return Response(
                    content="Log file too large to retrieve",
                    media_type="text/plain"
                )
            f.seek(0, 0)
            content = f.read()
        return Response(content=content, media_type="text/plain")
    except FileNotFoundError:
        return Response(content="", media_type="text/plain")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)