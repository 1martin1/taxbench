from fastapi import FastAPI, Response, Depends, Request, HTTPException
from pydantic import BaseModel, field_validator
import os
from datetime import datetime, timedelta

app = FastAPI()

class LogMessage(BaseModel):
    message: str

    @field_validator('message')
    def message_not_too_long(cls, v):
        max_length = 1024  # 1KB per message
        if len(v) > max_length:
            raise ValueError('Message too long')
        return v

# In-memory rate limiting storage
request_counts = {}

def get_client_ip(request: Request):
    if "X-Forwarded-For" in request.headers:
        return request.headers["X-Forwarded-For"].split(",")[0].strip()
    return request.client.host

def rate_limiter_dependency(request: Request):
    client_ip = get_client_ip(request)
    now = datetime.now()
    window = timedelta(minutes=1)
    limit = 10

    # Clean up old timestamps
    timestamps = request_counts.get(client_ip, [])
    new_timestamps = [ts for ts in timestamps if now - ts <= window]
    request_counts[client_ip] = new_timestamps

    if len(new_timestamps) >= limit:
        raise HTTPException(status_code=429, detail="Too Many Requests")

    # Add current timestamp
    request_counts.setdefault(client_ip, []).append(now)

@app.post("/log")
async def log_message(data: LogMessage, dep: None = Depends(rate_limiter_dependency)):
    try:
        with open("logs.txt", "a", encoding="utf-8") as f:
            f.write(data.message + "\n")
        return Response(status_code=200)
    except (IOError, OSError) as e:
        return Response(content="Internal Server Error", status_code=500)
    except Exception as e:
        return Response(content="Internal Server Error", status_code=500)

@app.get("/logs")
async def get_logs():
    try:
        file_size = os.path.getsize("logs.txt")
        MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
        if file_size > MAX_FILE_SIZE:
            return Response(content=f"Log file too large ({file_size} bytes)", status_code=413)
        with open("logs.txt", "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        content = ""
    except (IOError, OSError) as e:
        return Response(content="Internal Server Error", status_code=500)
    except Exception as e:
        return Response(content="Internal Server Error", status_code=500)
    return Response(content=content, media_type="text/plain")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)