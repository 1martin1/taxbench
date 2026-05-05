import os
from fastapi import FastAPI, Response, HTTPException, Depends, Header, status
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

LOGS_API_KEY = os.getenv("LOGS_API_KEY", "supersecret")

app = FastAPI()

class LogEntry(BaseModel):
    message: str

def get_api_key(authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication scheme",
        )
    token = authorization[7:].strip()
    if token != LOGS_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    return token

@app.post("/log")
def create_log(entry: LogEntry):
    try:
        with open("logs.txt", "a") as f:
            f.write(entry.message + "\n")
        return Response(status_code=200)
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/logs")
def read_logs(api_key: str = Depends(get_api_key)):
    try:
        if not os.path.exists("logs.txt"):
            return ""
        with open("logs.txt", "r") as f:
            return f.read()
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)