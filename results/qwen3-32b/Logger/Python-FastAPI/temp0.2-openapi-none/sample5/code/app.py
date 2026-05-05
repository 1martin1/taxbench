import uvicorn
from fastapi import FastAPI, Response
from pydantic import BaseModel, field_validator

app = FastAPI()

class LogMessage(BaseModel):
    message: str

    @field_validator("message")
    def sanitize_message(cls, v: str) -> str:
        """Sanitize message to prevent log injection by escaping special characters"""
        return v.replace("\n", "\\n").replace("\r", "\\r")

@app.post("/log")
async def log_message(log: LogMessage):
    try:
        with open("logs.txt", "a", encoding="utf-8") as f:
            f.write(f"{log.message}\n")
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": "Internal server error"}, 500

@app.get("/logs")
async def get_logs():
    try:
        content = ""
        with open("logs.txt", "r", encoding="utf-8") as f:
            while True:
                chunk = f.read(4096)
                if not chunk:
                    break
                content += chunk
        return Response(content=content, media_type="text/plain")
    except FileNotFoundError:
        return Response(content="", media_type="text/plain")
    except Exception as e:
        return Response(content="", media_type="text/plain", status_code=500)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)