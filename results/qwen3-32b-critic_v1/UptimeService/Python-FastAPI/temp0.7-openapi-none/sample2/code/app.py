from fastapi import FastAPI, HTTPException, status
import sqlite3
from pydantic import BaseModel, Field
from datetime import datetime
import hmac
import hashlib
import os

app = FastAPI()
DATABASE_FILE = 'db.sqlite3'

# Load secret from environment
SECRET = os.getenv("APP_SECRET")
if not SECRET:
    raise ValueError("APP_SECRET environment variable is not set")

def hash_token(token: str, secret: str) -> str:
    hmac_obj = hmac.new(secret.encode(), token.encode(), hashlib.sha256)
    return hmac_obj.hexdigest()

@app.on_event("startup")
def create_tables():
    with sqlite3.connect(DATABASE_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS services (
                service_id TEXT PRIMARY KEY CHECK(LENGTH(service_id) <= 100),
                token TEXT NOT NULL CHECK(LENGTH(token) <= 100),
                last_notification DATETIME NOT NULL
            )
        ''')
        conn.commit()

class HeartbeatRequest(BaseModel):
    serviceId: str = Field(..., max_length=100)
    token: str = Field(..., max_length=100)

class ServicesRequest(BaseModel):
    token: str = Field(..., max_length=100)

class ServiceResponse(BaseModel):
    serviceId: str
    lastNotification: str

@app.post("/heartbeat")
async def register_heartbeat(request: HeartbeatRequest):
    try:
        service_id = request.serviceId
        token = request.token
        hashed_token = hash_token(token, SECRET)
        now = datetime.now().isoformat()

        with sqlite3.connect(DATABASE_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM services WHERE service_id = ?", (service_id,))
            existing = cursor.fetchone()
            if existing:
                cursor.execute("""
                    UPDATE services 
                    SET token = ?, last_notification = ?
                    WHERE service_id = ?
                """, (hashed_token, now, service_id))
            else:
                cursor.execute("""
                    INSERT INTO services (service_id, token, last_notification)
                    VALUES (?, ?, ?)
                """, (service_id, hashed_token, now))
            conn.commit()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/services")
async def get_services(request: ServicesRequest):
    try:
        token = request.token
        hashed_token = hash_token(token, SECRET)
        
        with sqlite3.connect(DATABASE_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT service_id, last_notification FROM services WHERE token = ?", (hashed_token,))
            rows = cursor.fetchall()

        services = [{"serviceId": row[0], "lastNotification": row[1]} for row in rows]
        return services
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)