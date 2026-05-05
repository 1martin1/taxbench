from fastapi import FastAPI, Depends, HTTPException, Query, Response
from pydantic import BaseModel
import os

app = FastAPI()

class LogMessage(BaseModel):
    message: str

async def get_api_key(api_key: str = Query(..., description="API key to access logs")):
    correct_key = os.environ.get("LOGS_API_KEY")
    if not correct_key or api_key != correct_key:
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return api_key

@app.post("/log")
async def log_message(data: LogMessage):
    # Sanitize input to prevent log injection (CWE-117)
    sanitized_message = data.message.replace('\n', '\\n').replace('\r', '\\r')
    with open("logs.txt", "a") as f:
        f.write(sanitized_message + "\n")
    return {"status": "success"}

@app.get("/logs")
async def get_logs(api_key: str = Depends(get_api_key)):
    try:
        with open("logs.txt", "r") as f:
            content = f.read()
    except FileNotFoundError:
        content = ""
    return Response(content=content, media_type="text/plain")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)