import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator, List

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict


DB_PATH = "db.sqlite3"


app = FastAPI(
    title="Service Monitoring API",
    version="1.0.0",
    description="API for monitoring the status of services.",
)


class HeartbeatRequest(BaseModel):
    serviceId: str
    token: str


class ServicesRequest(BaseModel):
    token: str


class ServiceStatusResponse(BaseModel):
    serviceId: str
    lastNotification: datetime

    model_config = ConfigDict(from_attributes=True)


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
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


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.post(
    "/heartbeat",
    status_code=200,
    responses={
        200: {"description": "Heartbeat registered successfully."},
        400: {"description": "Invalid request payload."},
        500: {"description": "Internal server error."},
    },
)
def register_heartbeat(payload: HeartbeatRequest):
    try:
        service_id = payload.serviceId.strip()
        token = payload.token.strip()

        if not service_id or not token:
            raise HTTPException(status_code=400, detail="serviceId and token must be non-empty strings")

        now = datetime.now(timezone.utc).isoformat()

        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO service_heartbeats (service_id, token, last_notification)
                VALUES (?, ?, ?)
                ON CONFLICT(service_id, token)
                DO UPDATE SET last_notification = excluded.last_notification
                """,
                (service_id, token, now),
            )
            conn.commit()

        return {"message": "Heartbeat registered successfully."}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post(
    "/services",
    response_model=List[ServiceStatusResponse],
    responses={
        200: {
            "description": "A list of registered services with their last notification date (shows all services registered with the given token)."
        },
        500: {"description": "Internal server error."},
    },
)
def get_services(payload: ServicesRequest):
    try:
        token = payload.token.strip()
        if not token:
            return []

        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT service_id, last_notification
                FROM service_heartbeats
                WHERE token = ?
                ORDER BY service_id ASC
                """,
                (token,),
            ).fetchall()

        return [
            ServiceStatusResponse(
                serviceId=row["service_id"],
                lastNotification=datetime.fromisoformat(row["last_notification"]),
            )
            for row in rows
        ]
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)