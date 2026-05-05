from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel
import sqlite3
from datetime import datetime, timezone

app = FastAPI()

class HeartbeatRequest(BaseModel):
    serviceId: str
    token: str

class ServicesRequest(BaseModel):
    token: str

@app.on_event("startup")
def create_table():
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS services (
            service_id TEXT NOT NULL,
            token TEXT NOT NULL,
            last_notification DATETIME NOT NULL,
            UNIQUE(service_id, token)
        )
    ''')
    conn.commit()
    conn.close()

@app.post("/heartbeat")
async def register_heartbeat(request: HeartbeatRequest):
    try:
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM services WHERE service_id = ? AND token = ?", 
                       (request.serviceId, request.token))
        existing = cursor.fetchone()
        current_time = datetime.now(timezone.utc).isoformat()
        if existing:
            cursor.execute("""
                UPDATE services 
                SET last_notification = ? 
                WHERE service_id = ? AND token = ?
            """, (current_time, request.serviceId, request.token))
        else:
            cursor.execute("""
                INSERT INTO services (service_id, token, last_notification)
                VALUES (?, ?, ?)
            """, (request.serviceId, request.token, current_time))
        conn.commit()
        conn.close()
        return {"status": "Heartbeat registered successfully"}
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/services")
async def get_services(request: ServicesRequest):
    try:
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        cursor.execute("SELECT service_id, last_notification FROM services WHERE token = ?", (request.token,))
        rows = cursor.fetchall()
        services = [{"serviceId": row[0], "lastNotification": row[1]} for row in rows]
        conn.close()
        return services
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)