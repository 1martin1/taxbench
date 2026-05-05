import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import List

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field


DB_PATH = "db.sqlite3"


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(get_db_connection()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS service_heartbeats (
                service_id TEXT NOT NULL,
                token TEXT NOT NULL,
                last_notification TEXT NOT NULL,
                PRIMARY KEY (service_id, token)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_service_heartbeats_token ON service_heartbeats(token)"
        )
        conn.commit()


class HeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    serviceId: str = Field(..., description="The unique identifier of the service.")
    token: str = Field(
        ..., description="A unique token to prevent unauthorized users from reading the status of the service."
    )


class ServicesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(
        ..., description="A token to authenticate for reading specific service statuses."
    )


class ServiceStatusResponse(BaseModel):
    serviceId: str
    lastNotification: str


app = FastAPI(
    title="Service Monitoring API",
    version="1.0.0",
    description="API for monitoring the status of services.",
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.post("/heartbeat", status_code=200)
def register_heartbeat(payload: HeartbeatRequest):
    try:
        now = datetime.now(timezone.utc).isoformat()
        with closing(get_db_connection()) as conn:
            conn.execute(
                """
                INSERT INTO service_heartbeats (service_id, token, last_notification)
                VALUES (?, ?, ?)
                ON CONFLICT(service_id, token)
                DO UPDATE SET last_notification = excluded.last_notification
                """,
                (payload.serviceId, payload.token, now),
            )
            conn.commit()
        return {"message": "Heartbeat registered successfully."}
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Internal server error.") from exc


@app.post("/services", response_model=List[ServiceStatusResponse], status_code=200)
def get_services_overview(payload: ServicesRequest):
    try:
        with closing(get_db_connection()) as conn:
            rows = conn.execute(
                """
                SELECT service_id, last_notification
                FROM service_heartbeats
                WHERE token = ?
                ORDER BY service_id ASC
                """,
                (payload.token,),
            ).fetchall()

        return [
            ServiceStatusResponse(
                serviceId=row["service_id"],
                lastNotification=row["last_notification"],
            )
            for row in rows
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Internal server error.") from exc


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)