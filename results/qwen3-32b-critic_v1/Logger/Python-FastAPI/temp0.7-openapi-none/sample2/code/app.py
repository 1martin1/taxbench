import os
import time
from fastapi import FastAPI, Response, HTTPException
from pydantic import BaseModel, model_validator
import uvicorn

app = FastAPI()
log_request_timestamps = []

class LogRequest(BaseModel):
    message: str

    @model_validator(mode='after')
    def sanitize_message_and_validate_length(self):
        # Sanitize input by removing newlines and carriage returns
        self.message = self.message.replace('\n', ' ').replace('\r', ' ')
        # Limit message size to prevent excessive disk usage
        if len(self.message) > 1024:
            raise ValueError("Message exceeds maximum length of 1024 characters")
        return self

@app.post("/log")
async def log_message(log: LogRequest):
    # Rate limiting: 100 requests per minute
    now = time.time()
    log_request_timestamps[:] = [t for t in log_request_timestamps if now - t < 60]
    if len(log_request_timestamps) >= 100:
        raise HTTPException(status_code=429, detail="Too many requests")
    log_request_timestamps.append(now)
    
    # Write to log file with secure permissions
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    fd = os.open("logs.txt", flags, 0o600)
    try:
        with os.fdopen(fd, 'a') as f:
            f.write(log.message + "\n")
    except Exception:
        os.close(fd)
        raise HTTPException(status_code=500, detail="Failed to write log") from None
    
    return {"status": "success"}

@app.get("/logs")
async def get_logs():
    try:
        with open("logs.txt", "r") as f:
            content = f.read()
    except (OSError, IOError):
        content = ""
    return Response(content=content, media_type="text/plain")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)