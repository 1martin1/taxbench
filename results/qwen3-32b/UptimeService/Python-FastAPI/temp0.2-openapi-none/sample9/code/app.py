import sqlite3
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from datetime import datetime, timezone
import uvicorn

app = FastAPI()

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={"detail": "Invalid request payload"},
    )

@app.on_event("startup")
def create_tables():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS services (
            service_id TEXT NOT NULL,
            token TEXT NOT NULL,
            last_notification DATETIME NOT NULL,
            PRIMARY KEY (service_id, token)
        )
    ''')
    conn.commit()
    conn.close()

class HeartbeatRequest(BaseModel):
    serviceId: str
    token: str

class ServicesRequest(BaseModel):
    token: str

class ServiceResponse(BaseModel):
    serviceId: str
    lastNotification: datetime

@app.post("/heartbeat")
async def register_heartbeat(request: HeartbeatRequest):
    service_id = request.serviceId
    token = request.token
    current_time = datetime.now(timezone.utc).isoformat()
    try:
        conn = sqlite3.connect("db.sqlite3")
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO services (service_id, token, last_notification)
            VALUES (?, ?, ?)
            ON CONFLICT(service_id, token) DO UPDATE SET
                last_notification = excluded.last_notification
        ''', (service_id, token, current_time))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        conn.close()
    return Response(status_code=200)

@app.post("/services")
async def get_services(request: ServicesRequest):
    token = request.token
    try:
        conn = sqlite3.connect("db.sqlite3")
        cursor = conn.cursor()
        cursor.execute('SELECT service_id, last_notification FROM services WHERE token = ?', (token,))
        rows = cursor.fetchall()
        services = [{"serviceId": row[0], "lastNotification": row[1]} for row in rows]
        return services
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        conn.close()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)