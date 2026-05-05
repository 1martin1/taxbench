from fastapi import FastAPI, HTTPException, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import sqlite3
from pydantic import BaseModel
from datetime import datetime, timezone
from typing import List

app = FastAPI()

class HeartbeatRequest(BaseModel):
    serviceId: str
    token: str

class HeartbeatResponse(BaseModel):
    status: str

class ServicesRequest(BaseModel):
    token: str

class ServiceStatus(BaseModel):
    serviceId: str
    lastNotification: str

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "Invalid request payload"},
    )

@app.on_event("startup")
def startup():
    db = sqlite3.connect('db.sqlite3')
    db.execute('''
        CREATE TABLE IF NOT EXISTS services (
            service_id TEXT NOT NULL,
            token TEXT NOT NULL,
            last_notification TEXT NOT NULL,
            PRIMARY KEY (service_id, token)
        )
    ''')
    db.close()

@app.post("/heartbeat", response_model=HeartbeatResponse)
async def register_heartbeat(request: HeartbeatRequest):
    service_id = request.serviceId
    token = request.token
    try:
        current_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect('db.sqlite3') as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO services (service_id, token, last_notification)
                VALUES (?, ?, ?)
                ON CONFLICT(service_id, token) DO UPDATE SET
                    last_notification = excluded.last_notification
            ''', (service_id, token, current_time))
            conn.commit()
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Internal server error")
    return {"status": "Heartbeat registered successfully"}

@app.post("/services", response_model=List[ServiceStatus])
async def get_services(request: ServicesRequest):
    token = request.token
    try:
        with sqlite3.connect('db.sqlite3') as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT service_id, last_notification
                FROM services
                WHERE token = ?
            ''', (token,))
            rows = cursor.fetchall()
            services = []
            for row in rows:
                iso_time = f"{row[1].replace(' ', 'T')}Z"
                services.append({
                    "serviceId": row[0],
                    "lastNotification": iso_time
                })
            return services
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)