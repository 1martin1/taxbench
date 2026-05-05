import sqlite3
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, field_validator
from datetime import datetime
from passlib.hash import bcrypt

app = FastAPI()

class HeartbeatRequest(BaseModel):
    serviceId: str
    token: str

    @field_validator('serviceId', 'token')
    def not_empty(cls, value: str) -> str:
        if not value or value.strip() == '':
            raise ValueError('Field cannot be empty')
        return value

class ServicesRequest(BaseModel):
    token: str

    @field_validator('token')
    def not_empty(cls, value: str) -> str:
        if not value or value.strip() == '':
            raise ValueError('Field cannot be empty')
        return value

class ServiceStatus(BaseModel):
    serviceId: str
    lastNotification: datetime

@app.on_event("startup")
def create_table():
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS services (
            service_id TEXT NOT NULL PRIMARY KEY,
            token TEXT NOT NULL,
            last_notification DATETIME NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

@app.post("/heartbeat")
async def register_heartbeat(data: HeartbeatRequest):
    try:
        service_id = data.serviceId
        token = data.token
        hashed_token = bcrypt.hash(token)
        now = datetime.now().isoformat()
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO services (service_id, token, last_notification)
            VALUES (?, ?, ?)
        ''', (service_id, hashed_token, now))
        conn.commit()
        conn.close()
        return {"status": "Heartbeat registered successfully"}
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/services")
async def get_services(data: ServicesRequest):
    try:
        token = data.token
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        cursor.execute('SELECT service_id, token, last_notification FROM services')
        rows = cursor.fetchall()
        conn.close()
        services = []
        for row in rows:
            service_id, stored_hash, last_notification = row
            if bcrypt.verify(token, stored_hash):
                services.append(ServiceStatus(serviceId=service_id, lastNotification=last_notification))
        return services
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)