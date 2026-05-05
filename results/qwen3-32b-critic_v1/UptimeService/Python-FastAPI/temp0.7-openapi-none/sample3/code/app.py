from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel, Field
import sqlite3
import datetime
from passlib.hash import bcrypt

app = FastAPI()

class RateLimiter:
    def __init__(self, max_requests, window_seconds):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests = {}

    def is_allowed(self, key):
        now = datetime.datetime.now()
        window_start = now - datetime.timedelta(seconds=self.window_seconds)
        if key not in self.requests:
            self.requests[key] = []
        self.requests[key] = [req_time for req_time in self.requests[key] if req_time >= window_start]
        if len(self.requests[key]) >= self.max_requests:
            return False
        self.requests[key].append(now)
        return True

heartbeat_limiter = RateLimiter(max_requests=10, window_seconds=60)
services_limiter = RateLimiter(max_requests=5, window_seconds=60)

class HeartbeatRequest(BaseModel):
    serviceId: str = Field(..., min_length=1, max_length=255)
    token: str = Field(..., min_length=1, max_length=255)

class ServicesRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=255)

class ServiceResponse(BaseModel):
    serviceId: str
    lastNotification: str

def check_rate_limit(limiter, key):
    if not limiter.is_allowed(key):
        raise HTTPException(status_code=429, detail="Too many requests")

@app.post("/heartbeat")
async def heartbeat(request: HeartbeatRequest):
    rate_key = f"heartbeat:{request.serviceId}:{request.token}"
    check_rate_limit(heartbeat_limiter, rate_key)
    
    try:
        hashed_token = bcrypt.hash(request.token)
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS services (
                service_id TEXT NOT NULL,
                token TEXT NOT NULL,
                last_notification DATETIME NOT NULL,
                PRIMARY KEY (service_id, token)
            )
        ''')
        current_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cursor.execute('''
            INSERT INTO services (service_id, token, last_notification)
            VALUES (?, ?, ?)
            ON CONFLICT(service_id, token) DO UPDATE SET
                last_notification = excluded.last_notification
        ''', (request.serviceId, hashed_token, current_time))
        conn.commit()
        conn.close()
        return {"status": "Heartbeat registered successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/services")
async def get_services(request: ServicesRequest):
    rate_key = f"services:{request.token}"
    check_rate_limit(services_limiter, rate_key)
    
    try:
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        cursor.execute('SELECT service_id, token, last_notification FROM services')
        rows = cursor.fetchall()
        conn.close()
        
        valid_services = []
        for row in rows:
            service_id, stored_hash, last_notification = row
            if bcrypt.verify(request.token, stored_hash):
                valid_services.append(ServiceResponse(serviceId=service_id, lastNotification=last_notification))
        
        return valid_services
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)