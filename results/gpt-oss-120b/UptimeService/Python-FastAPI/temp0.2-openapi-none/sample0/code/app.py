import os
import sqlite3
from datetime import datetime, timezone
from typing import List

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# Initialize FastAPI app
app = FastAPI(
    title="Service Monitoring API",
    version="1.0.0",
    description="API for monitoring the status of services."
)

# Database setup
DB_PATH = "db.sqlite3"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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


# Pydantic models
class HeartbeatRequest(BaseModel):
    serviceId: str = Field(..., description="The unique identifier of the service.")
    token: str = Field(..., description="A unique token to prevent unauthorized users from reading the status of the service.")


class ServicesRequest(BaseModel):
    token: str = Field(..., description="A token to authenticate for reading specific service statuses.")


class ServiceInfo(BaseModel):
    serviceId: str = Field(..., description="The unique identifier of the service.")
    lastNotification: datetime = Field(..., description="The last time a heartbeat was received for the service.")


# Helper functions
def _get_current_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _upsert_service(service_id: str, token: str) -> None:
    now_iso = _get_current_utc_iso()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO services (service_id, token, last_notification)
                VALUES (?, ?, ?)
                ON CONFLICT(service_id, token) DO UPDATE SET last_notification=excluded.last_notification
                """,
                (service_id, token, now_iso),
            )
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Database error while registering heartbeat.") from e


def _fetch_services_by_token(token: str) -> List[ServiceInfo]:
    try:
        cur = conn.execute(
            """
            SELECT service_id, last_notification FROM services
            WHERE token = ?
            ORDER BY service_id ASC
            """,
            (token,),
        )
        rows = cur.fetchall()
        return [
            ServiceInfo(
                serviceId=row[0],
                lastNotification=datetime.fromisoformat(row[1]),
            )
            for row in rows
        ]
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Database error while fetching services.") from e


# Routes
@app.post("/heartbeat", status_code=status.HTTP_200_OK, summary="Register a heartbeat for a service")
async def register_heartbeat(payload: HeartbeatRequest):
    """
    Accepts a request to signify that a specific service is still alive.
    """
    _upsert_service(payload.serviceId, payload.token)
    return {"detail": "Heartbeat registered successfully."}


@app.post(
    "/services",
    response_model=List[ServiceInfo],
    status_code=status.HTTP_200_OK,
    summary="Get overview of registered services",
)
async def get_services(payload: ServicesRequest):
    """
    Provides an overview of registered services and their last notification date,
    where the token of this request matches the token sent with the service request.
    """
    services = _fetch_services_by_token(payload.token)
    return services


# Global exception handler for unexpected errors
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error."},
    )


# Run the application
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)