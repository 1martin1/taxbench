from fastapi import FastAPI, Response, HTTPException, Request
from pydantic import BaseModel, Field
import os
from datetime import datetime, timedelta

app = FastAPI()

class LogEntry(BaseModel):
    message: str = Field(..., max_length=1024)  # Limit message size

LOG_FILE = "logs.txt"
MAX_LOG_SIZE = 1024 * 1024  # 1MB for memory safety
RATE_LIMIT = 100  # Requests per minute
request_times = {}  # Track request timestamps per IP

@app.post("/log")
async def log_message(entry: LogEntry, request: Request):
    client_ip = request.client.host
    now = datetime.now()
    
    # Rate limiting
    if client_ip in request_times:
        # Remove timestamps older than 1 minute
        request_times[client_ip] = [t for t in request_times[client_ip] 
                                   if (now - t) < timedelta(minutes=1)]
        if len(request_times[client_ip]) >= RATE_LIMIT:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
    else:
        request_times[client_ip] = []
    
    request_times[client_ip].append(now)
    
    # Sanitize message to prevent log injection
    sanitized_message = entry.message.replace('\n', ' ').replace('\r', ' ')
    
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(sanitized_message + "\n")
    except (IOError, OSError) as e:
        raise HTTPException(status_code=500, detail="Failed to write log") from e
    
    return Response(status_code=200)

@app.get("/logs")
async def get_logs():
    try:
        if not os.path.exists(LOG_FILE):
            return ""
            
        file_size = os.path.getsize(LOG_FILE)
        if file_size > MAX_LOG_SIZE:
            raise HTTPException(status_code=413, detail="Log file too large")
            
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return f.read()
    except (IOError, OSError) as e:
        raise HTTPException(status_code=500, detail="Failed to read logs") from e

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)