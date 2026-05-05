import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import List

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


DB_PATH = "db.sqlite3"


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(get_db_connection()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS heartbeats (
                service_id TEXT NOT NULL,
                token TEXT NOT NULL,
                last_notification TEXT NOT NULL,
                PRIMARY KEY (service_id, token)
            )
            """
        )
        conn.commit()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class HeartbeatRequest(BaseModel):
    serviceId: str = Field(..., description="The unique identifier of the service.")
    token: str = Field(
        ..., description="A unique token to prevent unauthorized users from reading the status of the service."
    )


class ServicesRequest(BaseModel):
    token: str = Field(
        ..., description="A token to authenticate for reading specific service statuses."
    )


class ServiceStatus(BaseModel):
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
    if not payload.serviceId or not payload.token:
        raise HTTPException(status_code=400, detail="Invalid request payload.")

    timestamp = utc_now_iso()

    try:
        with closing(get_db_connection()) as conn:
            conn.execute(
                """
                INSERT INTO heartbeats (service_id, token, last_notification)
                VALUES (?, ?, ?)
                ON CONFLICT(service_id, token)
                DO UPDATE SET last_notification = excluded.last_notification
                """,
                (payload.serviceId, payload.token, timestamp),
            )
            conn.commit()
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error.")

    return {"message": "Heartbeat registered successfully."}


@app.post("/services", response_model=List[ServiceStatus], status_code=200)
def get_services(payload: ServicesRequest):
    try:
        with closing(get_db_connection()) as conn:
            rows = conn.execute(
                """
                SELECT service_id, last_notification
                FROM heartbeats
                WHERE token = ?
                ORDER BY service_id ASC
                """,
                (payload.token,),
            ).fetchall()
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error.")

    return [
        ServiceStatus(
            serviceId=row["service_id"],
            lastNotification=row["last_notification"],
        )
        for row in rows
    ]


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)