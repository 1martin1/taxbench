from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import sqlite3
from pydantic import BaseModel, Field
from datetime import datetime
from typing import List
import hashlib

app = FastAPI()

@app.on_event("startup")
def create_tables():
    conn = sqlite3.connect("db.sqlite3")
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

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(status_code=400, content={"detail": "Invalid request payload"})

class HeartbeatRequest(BaseModel):
    serviceId: str = Field(..., max_length=255)
    token: str = Field(..., max_length=255)

class ServicesRequest(BaseModel):
    token: str = Field(..., max_length=255)

class ServiceResponse(BaseModel):
    serviceId: str
    lastNotification: datetime

@app.post("/heartbeat")
async def post_heartbeat(request: HeartbeatRequest):
    service_id = request.serviceId
    token = request.token
    
    # Hash token for secure storage
    token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
    
    # Rate limiting: 10 seconds between heartbeats for same service/token
    conn = sqlite3.connect("db.sqlite3")
    c = conn.cursor()
    try:
        # Check existing entry for rate limiting
        c.execute('SELECT last_notification FROM services WHERE service_id = ? AND token = ?', 
                 (service_id, token_hash))
        row = c.fetchone()
        if row:
            last_time = datetime.fromisoformat(row[0])
            if (datetime.now() - last_time).total_seconds() < 10:  # 10 seconds
                raise HTTPException(status_code=429, detail="Rate limit exceeded")
        
        # Store heartbeat
        now = datetime.now().isoformat()
        c.execute('''
            INSERT INTO services (service_id, token, last_notification)
            VALUES (?, ?, ?)
            ON CONFLICT(service_id, token) DO UPDATE SET
                last_notification = excluded.last_notification
        ''', (service_id, token_hash, now))
        conn.commit()
        return {"status": "Heartbeat registered successfully"}
    except sqlite3.Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        conn.close()

@app.post("/services", response_model=List[ServiceResponse])
async def get_services(request: ServicesRequest):
    token = request.token
    token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
    
    conn = sqlite3.connect("db.sqlite3")
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    try:
        c.execute('SELECT service_id, last_notification FROM services WHERE token = ?', (token_hash,))
        rows = c.fetchall()
        
        # Prevent memory exhaustion by limiting results
        if len(rows) > 1000:
            raise HTTPException(status_code=429, detail="Too many services")
            
        services = []
        for row in rows:
            last_notification = datetime.fromisoformat(row['last_notification'])
            services.append(ServiceResponse(
                serviceId=row['service_id'], 
                lastNotification=last_notification
            ))
        return services
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        conn.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)