import sqlite3
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime
from typing import List
import uvicorn

app = FastAPI()

class HeartbeatRequest(BaseModel):
    serviceId: str
    token: str

class ServicesRequest(BaseModel):
    token: str

class ServiceResponse(BaseModel):
    serviceId: str
    lastNotification: datetime

@app.on_event("startup")
def startup():
    conn = sqlite3.connect("db.sqlite3")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS service_heartbeats (
            service_id TEXT NOT NULL,
            token TEXT NOT NULL,
            last_notification DATETIME NOT NULL,
            PRIMARY KEY (service_id, token)
        )
    """)
    conn.commit()
    conn.close()

@app.post("/heartbeat")
async def register_heartbeat(heartbeat: HeartbeatRequest):
    try:
        conn = sqlite3.connect("db.sqlite3")
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO service_heartbeats 
            (service_id, token, last_notification)
            VALUES (?, ?, datetime('now'))
        """, (heartbeat.serviceId, heartbeat.token))
        conn.commit()
        conn.close()
        return {"status": "Heartbeat registered successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/services", response_model=List[ServiceResponse])
async def get_services_endpoint(request: ServicesRequest):
    try:
        conn = sqlite3.connect("db.sqlite3")
        cursor = conn.cursor()
        cursor.execute("""
            SELECT service_id, last_notification 
            FROM service_heartbeats
            WHERE token = ?
        """, (request.token,))
        rows = cursor.fetchall()
        conn.close()
        services = []
        for row in rows:
            service_id = row[0]
            dt_str = row[1]
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            services.append({"serviceId": service_id, "lastNotification": dt})
        return services
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)