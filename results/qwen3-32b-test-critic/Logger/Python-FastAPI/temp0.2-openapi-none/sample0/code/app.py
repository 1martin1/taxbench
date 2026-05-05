import os
from fastapi import FastAPI, Response, HTTPException, Request
from pydantic import BaseModel

app = FastAPI()

class LogEntry(BaseModel):
    message: str

@app.post("/log")
async def log_message(log_entry: LogEntry):
    try:
        with open("logs.txt", "a") as f:
            f.write(log_entry.message + "\n")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "success"}

@app.get("/logs")
async def get_logs(request: Request):
    api_key = request.headers.get("X-API-Key")
    if not api_key or api_key != "supersecretkey":
        raise HTTPException(status_code=403, detail="Authentication required")
    try:
        if not os.path.exists("logs.txt"):
            return Response(content="", media_type="text/plain")
        with open("logs.txt", "r") as f:
            content = f.read()
        return Response(content=content, media_type="text/plain")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)