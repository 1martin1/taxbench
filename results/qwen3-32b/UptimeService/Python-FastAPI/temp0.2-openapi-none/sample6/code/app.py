from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel
import sqlite3
from datetime import datetime
import os

class HeartbeatRequest(BaseModel):
    serviceId: str
    token: str

class ServicesRequest(BaseModel):
    token: str

class ServiceResponse(BaseModel):
    serviceId: str
    lastNotification: datetime

app = FastAPI()

@app.on_event("startup")
def startup():
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS service_heartbeats (
            service_id TEXT NOT NULL,
            token TEXT NOT NULL,
            last_notification TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (service_id, token)
        )
    ''')
    conn.commit()
    conn.close()

@app.post("/heartbeat")
async def register_heartbeat(request: HeartbeatRequest):
    service_id = request.serviceId
    token = request.token
    try:
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO service_heartbeats 
            (service_id, token, last_notification)
            VALUES (?, ?, datetime('now'))
        ''', (service_id, token))
        conn.commit()
        conn.close()
        return {"status": "success"}
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Internal server error")
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid request payload")

@app.post("/services", response_model=list[ServiceResponse])
async def get_services(request: ServicesRequest):
    token = request.token
    try:
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT service_id, last_notification 
            FROM service_heartbeats 
            WHERE token = ?
        ''', (token,))
        rows = cursor.fetchall()
        conn.close()
        services = [{"serviceId": row[0], "lastNotification": row[1]} for row in rows]
        return services
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Internal server error")
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)