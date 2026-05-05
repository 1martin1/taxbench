import os
import sqlite3
from datetime import datetime, timezone
from typing import List

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

app = FastAPI(title="Service Monitoring API", version="1.0.0", description="API for monitoring the status of services.")


DB_PATH = "db.sqlite3"


def get_db_connection():
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS services (
                service_id TEXT NOT NULL,
                token TEXT NOT NULL,
                last_notification TIMESTAMP NOT NULL,
                PRIMARY KEY (service_id, token)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


@app.on_event("startup")
def on_startup():
    init_db()


class HeartbeatRequest(BaseModel):
    serviceId: str = Field(..., description="The unique identifier of the service.")
    token: str = Field(..., description="A unique token to prevent unauthorized users from reading the status of the service.")


class ServicesRequest(BaseModel):
    token: str = Field(..., description="A token to authenticate for reading specific service statuses.")


class ServiceInfo(BaseModel):
    serviceId: str = Field(..., description="The unique identifier of the service.")
    lastNotification: datetime = Field(..., description="The last time a heartbeat was received for the service.")


@app.post("/heartbeat", status_code=status.HTTP_200_OK, summary="Register a heartbeat for a service", description="Accepts a request to signify that a specific service is still alive.")
def register_heartbeat(payload: HeartbeatRequest):
    now = datetime.now(timezone.utc)
    conn = get_db_connection()
    try:
        # Try to insert; if exists, update the timestamp
        try:
            conn.execute(
                "INSERT INTO services (service_id, token, last_notification) VALUES (?, ?, ?)",
                (payload.serviceId, payload.token, now),
            )
        except sqlite3.IntegrityError:
            conn.execute(
                "UPDATE services SET last_notification = ? WHERE service_id = ? AND token = ?",
                (now, payload.serviceId, payload.token),
            )
        conn.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        conn.close()
    return {"detail": "Heartbeat registered successfully."}


@app.post("/services", response_model=List[ServiceInfo], status_code=status.HTTP_200_OK, summary="Get overview of registered services", description="Provides an overview of registered services and their last notification date, where the token of this request matches the token sent with the service request.")
def get_services(payload: ServicesRequest):
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT service_id, last_notification FROM services WHERE token = ?",
            (payload.token,),
        ).fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        conn.close()

    result = [
        ServiceInfo(
            serviceId=row["service_id"],
            lastNotification=row["last_notification"].replace(tzinfo=timezone.utc) if isinstance(row["last_notification"], datetime) else datetime.fromisoformat(row["last_notification"]).replace(tzinfo=timezone.utc)
        )
        for row in rows
    ]
    return result


# Run the application with uvicorn when executed directly
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)