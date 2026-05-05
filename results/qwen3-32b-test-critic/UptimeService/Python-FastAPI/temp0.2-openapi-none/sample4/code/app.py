from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime
import sqlite3

app = FastAPI()

@app.on_event("startup")
def create_tables():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS service_status (
            service_id TEXT NOT NULL,
            token TEXT NOT NULL,
            last_notification TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (service_id, token)
        )
    """)
    conn.commit()
    conn.close()

class HeartbeatRequest(BaseModel):
    serviceId: str
    token: str

class ServicesRequest(BaseModel):
    token: str

class ServiceStatusResponse(BaseModel):
    serviceId: str
    lastNotification: datetime

@app.post("/heartbeat")
async def register_heartbeat(request: HeartbeatRequest):
    service_id = request.serviceId
    token = request.token
    try:
        conn = sqlite3.connect("db.sqlite3")
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO service_status (service_id, token, last_notification)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(service_id, token) DO UPDATE SET
                last_notification = CURRENT_TIMESTAMP
        """, (service_id, token))
        conn.commit()
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        conn.close()
    return {"status": "Heartbeat registered successfully"}

@app.post("/services")
async def get_services(request: ServicesRequest):
    token = request.token
    try:
        conn = sqlite3.connect("db.sqlite3")
        cursor = conn.cursor()
        cursor.execute("""
            SELECT service_id, last_notification
            FROM service_status
            WHERE token = ?
        """, (token,))
        rows = cursor.fetchall()
        services = []
        for row in rows:
            service_id = row[0]
            ts_str = row[1]
            iso_str = ts_str.replace(' ', 'T', 1)
            last_notification = datetime.fromisoformat(iso_str)
            services.append(ServiceStatusResponse(serviceId=service_id, lastNotification=last_notification))
        return services
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        conn.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)