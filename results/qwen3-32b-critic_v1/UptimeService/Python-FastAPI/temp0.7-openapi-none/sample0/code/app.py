import sqlite3
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from passlib.context import CryptContext
from collections import defaultdict
import time

app = FastAPI()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
request_counts = defaultdict(list)

DATABASE_NAME = 'db.sqlite3'

def get_db():
    db = sqlite3.connect(DATABASE_NAME)
    db.row_factory = sqlite3.Row
    return db

@app.on_event("startup")
def create_tables():
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS services (
            service_id TEXT NOT NULL,
            token TEXT NOT NULL,
            last_notification TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (service_id, token)
        )
    """)
    db.commit()
    db.close()

class HeartbeatRequest(BaseModel):
    serviceId: str = Field(max_length=100)
    token: str = Field(max_length=100)

class ServicesRequest(BaseModel):
    token: str = Field(max_length=100)

@app.post("/heartbeat")
async def register_heartbeat(request: HeartbeatRequest):
    current_time = time.time()
    window = 60  # 1 minute
    max_requests = 10
    key = (request.serviceId, request.token)
    
    # Clean up old entries
    request_counts[key] = [t for t in request_counts[key] if t > current_time - window]
    
    if len(request_counts[key]) >= max_requests:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    
    request_counts[key].append(current_time)
    
    try:
        hashed_token = pwd_context.hash(request.token)
        db = get_db()
        db.execute("""
            INSERT INTO services (service_id, token, last_notification)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(service_id, token) DO UPDATE SET
                last_notification = datetime('now')
        """, (request.serviceId, hashed_token))
        db.commit()
        db.close()
        return {"status": "success"}
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/services")
async def get_services(request: ServicesRequest):
    try:
        hashed_token = pwd_context.hash(request.token)
        db = get_db()
        cursor = db.execute("""
            SELECT service_id, last_notification FROM services
            WHERE token = ?
        """, (hashed_token,))
        results = cursor.fetchall()
        db.close()
        services = [
            {"serviceId": row["service_id"], "lastNotification": row["last_notification"]}
            for row in results
        ]
        return services
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)