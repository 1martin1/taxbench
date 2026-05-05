import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, List

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator


DB_PATH = "db.sqlite3"


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(get_db_connection()) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
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


def parse_datetime_str(value: str) -> datetime:
    normalized = value
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    return datetime.fromisoformat(normalized)


class HeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    serviceId: str = Field(..., description="The unique identifier of the service.")
    token: str = Field(
        ...,
        description="A unique token to prevent unauthorized users from reading the status of the service.",
    )

    @field_validator("serviceId", "token")
    @classmethod
    def validate_non_empty_string(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("must be a string")
        if not value.strip():
            raise ValueError("must not be empty")
        return value


class ServicesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(
        ...,
        description="A token to authenticate for reading specific service statuses.",
    )

    @field_validator("token")
    @classmethod
    def validate_non_empty_token(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("must be a string")
        if not value.strip():
            raise ValueError("must not be empty")
        return value


class ServiceStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    serviceId: str
    lastNotification: datetime


app = FastAPI(
    title="Service Monitoring API",
    version="1.0.0",
    description="API for monitoring the status of services.",
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    if request.url.path == "/heartbeat":
        return JSONResponse(
            status_code=400,
            content={"detail": "Invalid request payload."},
        )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error."},
    )


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
def register_heartbeat(payload: HeartbeatRequest) -> Any:
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
                (payload.serviceId.strip(), payload.token.strip(), timestamp),
            )
            conn.commit()
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error.")

    return {"message": "Heartbeat registered successfully."}


@app.post(
    "/services",
    response_model=List[ServiceStatus],
    status_code=200,
    responses={
        200: {
            "description": "A list of registered services with their last notification date (shows all services registered with the given token)."
        },
        500: {"description": "Internal server error."},
    },
)
def get_services(payload: ServicesRequest) -> List[ServiceStatus]:
    try:
        with closing(get_db_connection()) as conn:
            rows = conn.execute(
                """
                SELECT service_id, last_notification
                FROM heartbeats
                WHERE token = ?
                ORDER BY service_id ASC
                """,
                (payload.token.strip(),),
            ).fetchall()
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error.")

    services: List[ServiceStatus] = []
    for row in rows:
        try:
            last_notification = parse_datetime_str(row["last_notification"])
        except Exception:
            raise HTTPException(status_code=500, detail="Internal server error.")
        services.append(
            ServiceStatus(
                serviceId=row["service_id"],
                lastNotification=last_notification,
            )
        )

    return services


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)