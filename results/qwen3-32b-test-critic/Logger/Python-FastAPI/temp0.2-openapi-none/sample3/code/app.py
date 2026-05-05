from fastapi import FastAPI, Response, HTTPException
from pydantic import BaseModel
import os

app = FastAPI()

class LogMessage(BaseModel):
    message: str

@app.post("/log")
async def log_message(log: LogMessage):
    # Sanitize input to prevent log injection and invalid characters
    sanitized_message = log.message.replace('\n', ' ').replace('\r', ' ')
    
    # Validate message is not empty after sanitization
    if not sanitized_message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    
    try:
        with open("logs.txt", "a", encoding="utf-8") as f:
            f.write(sanitized_message + "\n")
    except IOError as e:
        raise HTTPException(status_code=500, detail="Failed to write log") from e
    
    return Response(status_code=200)

@app.get("/logs")
async def get_logs():
    try:
        if not os.path.exists("logs.txt"):
            return Response(content="", media_type="text/plain")
            
        with open("logs.txt", "r", encoding="utf-8") as f:
            logs = f.read()
    except IOError as e:
        raise HTTPException(status_code=500, detail="Failed to read logs") from e
    return Response(content=logs, media_type="text/plain")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)