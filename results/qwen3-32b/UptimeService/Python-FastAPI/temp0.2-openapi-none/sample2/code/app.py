import sqlite3
import datetime
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List

app = FastAPI()

class HeartbeatRequest(BaseModel):
    serviceId: str
    token: str

class ServicesRequest(BaseModel):
    token: str

class ServiceStatus(BaseModel):
    serviceId: str
    lastNotification: str

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={"detail": "Invalid request payload"},
    )

@app.on_event("startup")
def create_table():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS service_heartbeats (
            service_id TEXT NOT NULL,
            token TEXT NOT NULL,
            last_notification TEXT NOT NULL,
            PRIMARY KEY (service_id, token)
        )
    """)
    conn.commit()
    conn.close()

@app.post("/heartbeat")
async def heartbeat(request: HeartbeatRequest):
    try:
        conn = sqlite3.connect("db.sqlite3")
        cursor = conn.cursor()
        current_time = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
        cursor.execute(
            "INSERT OR REPLACE INTO service_heartbeats (service_id, token, last_notification) VALUES (?, ?, ?)",
            (request.serviceId, request.token, current_time)
        )
        conn.commit()
        conn.close()
        return JSONResponse(status_code=200)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/services", response_model=List[ServiceStatus])
async def get_services(request: ServicesRequest):
    try:
        conn = sqlite3.connect("db.sqlite3")
        cursor = conn.cursor()
        cursor.execute("SELECT service_id, last_notification FROM service_heartbeats WHERE token = ?", (request.token,))
        rows = cursor.fetchall()
        conn.close()
        return [{"serviceId": row[0], "lastNotification": row[1]} for row in rows]
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)