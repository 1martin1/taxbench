import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import List

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, ConfigDict


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
        conn.commit()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class HeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    serviceId: str = Field(..., description="The unique identifier of the service.")
    token: str = Field(
        ...,
        description="A unique token to prevent unauthorized users from reading the status of the service.",
    )


class ServicesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(
        ...,
        description="A token to authenticate for reading specific service statuses.",
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


@app.post("/heartbeat", status_code=200, summary="Register a heartbeat for a service")
def register_heartbeat(payload: HeartbeatRequest):
    try:
        service_id = payload.serviceId.strip()
        token = payload.token.strip()

        if not service_id or not token:
            raise HTTPException(status_code=400, detail="serviceId and token must be non-empty strings.")

        timestamp = utc_now_iso()

        with closing(get_db_connection()) as conn:
            conn.execute(
                """
                INSERT INTO service_heartbeats (service_id, token, last_notification)
                VALUES (?, ?, ?)
                ON CONFLICT(service_id, token)
                DO UPDATE SET last_notification = excluded.last_notification
                """,
                (service_id, token, timestamp),
            )
            conn.commit()

        return {"message": "Heartbeat registered successfully."}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error.")


@app.post(
    "/services",
    response_model=List[ServiceStatus],
    status_code=200,
    summary="Get overview of registered services",
)
def get_services(payload: ServicesRequest):
    try:
        token = payload.token.strip()

        if not token:
            raise HTTPException(status_code=400, detail="token must be a non-empty string.")

        with closing(get_db_connection()) as conn:
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
            ServiceStatus(
                serviceId=row["service_id"],
                lastNotification=row["last_notification"],
            )
            for row in rows
        ]
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error.")


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)