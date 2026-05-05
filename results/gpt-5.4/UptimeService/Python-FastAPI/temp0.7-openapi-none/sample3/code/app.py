import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import List

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, ConfigDict


DB_PATH = "db.sqlite3"


app = FastAPI(
    title="Service Monitoring API",
    version="1.0.0",
    description="API for monitoring the status of services.",
)


class HeartbeatRequest(BaseModel):
    serviceId: str = Field(..., description="The unique identifier of the service.")
    token: str = Field(
        ...,
        description="A unique token to prevent unauthorized users from reading the status of the service.",
    )


class ServicesRequest(BaseModel):
    token: str = Field(
        ...,
        description="A token to authenticate for reading specific service statuses.",
    )


class ServiceOverview(BaseModel):
    serviceId: str
    lastNotification: datetime

    model_config = ConfigDict(from_attributes=True)


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS services (
                service_id TEXT NOT NULL,
                token TEXT NOT NULL,
                last_notification TEXT NOT NULL,
                PRIMARY KEY (service_id, token)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_services_token
            ON services (token)
            """
        )


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.post("/heartbeat", status_code=200)
def register_heartbeat(payload: HeartbeatRequest):
    service_id = payload.serviceId.strip()
    token = payload.token.strip()

    if not service_id or not token:
        raise HTTPException(status_code=400, detail="serviceId and token must not be empty.")

    now = datetime.now(timezone.utc).isoformat()

    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO services (service_id, token, last_notification)
                VALUES (?, ?, ?)
                ON CONFLICT(service_id, token)
                DO UPDATE SET last_notification = excluded.last_notification
                """,
                (service_id, token, now),
            )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error.")

    return {"message": "Heartbeat registered successfully."}


@app.post("/services", response_model=List[ServiceOverview], status_code=200)
def get_services(payload: ServicesRequest):
    token = payload.token.strip()

    if not token:
        raise HTTPException(status_code=400, detail="token must not be empty.")

    try:
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT service_id, last_notification
                FROM services
                WHERE token = ?
                ORDER BY service_id ASC
                """,
                (token,),
            ).fetchall()
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error.")

    result = [
        ServiceOverview(
            serviceId=row["service_id"],
            lastNotification=datetime.fromisoformat(row["last_notification"]),
        )
        for row in rows
    ]
    return result


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)