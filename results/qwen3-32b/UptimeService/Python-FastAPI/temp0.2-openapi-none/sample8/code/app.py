import sqlite3
import datetime
from fastapi import FastAPI, Response
from pydantic import BaseModel
from typing import List

app = FastAPI()

def init_db():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS services (
            service_id TEXT NOT NULL,
            token TEXT NOT NULL,
            last_notification DATETIME NOT NULL,
            PRIMARY KEY (service_id, token)
        )
    ''')
    conn.commit()
    conn.close()

@app.on_event("startup")
def on_startup():
    init_db()

class HeartbeatRequest(BaseModel):
    serviceId: str
    token: str

class ServicesRequest(BaseModel):
    token: str

class ServiceStatus(BaseModel):
    serviceId: str
    lastNotification: str

@app.post("/heartbeat")
async def register_heartbeat(request: HeartbeatRequest):
    service_id = request.serviceId
    token = request.token
    try:
        conn = sqlite3.connect('db.sqlite3')
        c = conn.cursor()
        current_time = datetime.datetime.now().isoformat()
        c.execute('''
            INSERT OR REPLACE INTO services (service_id, token, last_notification)
            VALUES (?, ?, ?)
        ''', (service_id, token, current_time))
        conn.commit()
        return Response(status_code=200)
    except Exception:
        return Response(status_code=500)
    finally:
        conn.close()

@app.post("/services")
async def get_services(request: ServicesRequest):
    token = request.token
    try:
        conn = sqlite3.connect('db.sqlite3')
        c = conn.cursor()
        c.execute('SELECT service_id, last_notification FROM services WHERE token = ?', (token,))
        rows = c.fetchall()
        services = [{"serviceId": row[0], "lastNotification": row[1]} for row in rows]
        return services
    except Exception:
        return Response(status_code=500)
    finally:
        conn.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)