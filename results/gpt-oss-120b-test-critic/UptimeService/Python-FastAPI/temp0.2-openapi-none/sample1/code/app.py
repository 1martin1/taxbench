import os
import sqlite3
from datetime import datetime, timezone
from typing import List

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ---------- Database Setup ----------
DB_PATH = "db.sqlite3"

def get_connection() -> sqlite3.Connection:
    # Using check_same_thread=False to allow usage across FastAPI threads
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_id TEXT NOT NULL,
                token TEXT NOT NULL,
                last_notification TEXT NOT NULL,
                UNIQUE(service_id, token)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

# ---------- Pydantic Models ----------
class HeartbeatRequest(BaseModel):
    serviceId: str = Field(..., description="The unique identifier of the service.")
    token: str = Field(..., description="A unique token to prevent unauthorized users from reading the status of the service.", min_length=1)

class ServicesRequest(BaseModel):
    token: str = Field(..., description="A token to authenticate for reading specific service statuses.", min_length=1)

class ServiceInfo(BaseModel):
    serviceId: str = Field(..., description="The unique identifier of the service.")
    lastNotification: datetime = Field(..., description="The last time a heartbeat was received for the service.")

# ---------- FastAPI App ----------
app = FastAPI(title="Service Monitoring API", version="1.0.0", description="API for monitoring the status of services.")

@app.on_event("startup")
def on_startup():
    init_db()

# ---------- Endpoints ----------
@app.post("/heartbeat", status_code=status.HTTP_200_OK, summary="Register a heartbeat for a service")
def register_heartbeat(payload: HeartbeatRequest):
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        # Try to insert; if conflict, update
        try:
            conn.execute(
                """
                INSERT INTO services (service_id, token, last_notification)
                VALUES (?, ?, ?)
                """,
                (payload.serviceId, payload.token, now_iso)
            )
        except sqlite3.IntegrityError:
            # Existing record, update timestamp
            conn.execute(
                """
                UPDATE services
                SET last_notification = ?
                WHERE service_id = ? AND token = ?
                """,
                (now_iso, payload.serviceId, payload.token)
            )
        conn.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        conn.close()
    return {"detail": "Heartbeat registered successfully."}

@app.post("/services", response_model=List[ServiceInfo], summary="Get overview of registered services")
def get_services(payload: ServicesRequest):
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            SELECT service_id, last_notification
            FROM services
            WHERE token = ?
            ORDER BY service_id ASC
            """,
            (payload.token,)
        )
        rows = cursor.fetchall()
        result = [
            ServiceInfo(
                serviceId=row["service_id"],
                lastNotification=datetime.fromisoformat(row["last_notification"])
            )
            for row in rows
        ]
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        conn.close()

# ---------- Custom Exception Handlers ----------
@app.exception_handler(HTTPException)
def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail}
    )

# ---------- Run Server ----------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)