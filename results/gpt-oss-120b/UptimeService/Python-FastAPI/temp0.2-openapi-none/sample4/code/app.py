import os
import sqlite3
from datetime import datetime, timezone
from typing import List

from fastapi import FastAPI, HTTPException, Depends, Request, status
from pydantic import BaseModel, Field

# Initialize FastAPI app
app = FastAPI(title="Service Monitoring API", version="1.0.0", description="API for monitoring the status of services.")

# Database file
DB_PATH = "db.sqlite3"

# Ensure the database and table exist
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS services (
            service_id TEXT NOT NULL,
            token TEXT NOT NULL,
            last_notification TEXT NOT NULL,
            PRIMARY KEY (service_id, token)
        )
        """
    )
    conn.commit()
    conn.close()

init_db()

# Dependency to get a DB connection
def get_db():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()

# Pydantic models for request/response bodies
class HeartbeatRequest(BaseModel):
    serviceId: str = Field(..., description="The unique identifier of the service.")
    token: str = Field(..., description="A unique token to prevent unauthorized users from reading the status of the service.", min_length=1)

class ServicesRequest(BaseModel):
    token: str = Field(..., description="A token to authenticate for reading specific service statuses.", min_length=1)

class ServiceInfo(BaseModel):
    serviceId: str = Field(..., description="The unique identifier of the service.")
    lastNotification: datetime = Field(..., description="The last time a heartbeat was received for the service.")

# Endpoint: Register a heartbeat
@app.post("/heartbeat", status_code=status.HTTP_200_OK, responses={
    200: {"description": "Heartbeat registered successfully."},
    400: {"description": "Invalid request payload."},
    500: {"description": "Internal server error."}
})
def register_heartbeat(payload: HeartbeatRequest, db: sqlite3.Connection = Depends(get_db)):
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        cursor = db.cursor()
        cursor.execute(
            """
            INSERT INTO services (service_id, token, last_notification)
            VALUES (?, ?, ?)
            ON CONFLICT(service_id, token) DO UPDATE SET
                last_notification=excluded.last_notification
            """,
            (payload.serviceId, payload.token, now_iso)
        )
        db.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error.") from e
    return {"detail": "Heartbeat registered successfully."}

# Endpoint: Get overview of registered services
@app.post("/services", response_model=List[ServiceInfo], responses={
    200: {"description": "A list of registered services with their last notification date."},
    500: {"description": "Internal server error."}
})
def get_services(payload: ServicesRequest, db: sqlite3.Connection = Depends(get_db)):
    try:
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT service_id, last_notification
            FROM services
            WHERE token = ?
            """,
            (payload.token,)
        )
        rows = cursor.fetchall()
        result = [
            ServiceInfo(
                serviceId=row[0],
                lastNotification=datetime.fromisoformat(row[1])
            )
            for row in rows
        ]
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error.") from e

# Run the application
if __name__ == "__main__":
    import uvicorn
    # Optional: use APP_SECRET for future extensions; currently not needed.
    _ = os.getenv("APP_SECRET")
    uvicorn.run(app, host="0.0.0.0", port=5000)