import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, List

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError


DB_PATH = "db.sqlite3"


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
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
            """
            CREATE INDEX IF NOT EXISTS idx_service_heartbeats_token
            ON service_heartbeats(token)
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


async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Service Monitoring API",
    version="1.0.0",
    description="API for monitoring the status of services.",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    if request.url.path == "/heartbeat":
        return JSONResponse(status_code=400, content={"detail": "Invalid request payload."})
    if request.url.path == "/services":
        return JSONResponse(status_code=500, content={"detail": "Internal server error."})
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


async def parse_json_body(request: Request) -> Any:
    try:
        return await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid request payload.")


@app.post(
    "/heartbeat",
    status_code=200,
    summary="Register a heartbeat for a service",
    responses={
        200: {"description": "Heartbeat registered successfully."},
        400: {"description": "Invalid request payload."},
        500: {"description": "Internal server error."},
    },
)
async def register_heartbeat(request: Request):
    try:
        body = await parse_json_body(request)
        payload = HeartbeatRequest.model_validate(body)

        service_id = payload.serviceId.strip()
        token = payload.token.strip()

        if not service_id or not token:
            raise HTTPException(status_code=400, detail="Invalid request payload.")

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
    except ValidationError:
        raise HTTPException(status_code=400, detail="Invalid request payload.")
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error.")


@app.post(
    "/services",
    response_model=List[ServiceStatus],
    status_code=200,
    summary="Get overview of registered services",
    responses={
        200: {
            "description": "A list of registered services with their last notification date (shows all services registered with the given token)."
        },
        500: {"description": "Internal server error."},
    },
)
async def get_services(request: Request):
    try:
        body = await parse_json_body(request)
        payload = ServicesRequest.model_validate(body)

        token = payload.token.strip()
        if not token:
            raise HTTPException(status_code=500, detail="Internal server error.")

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
    except ValidationError:
        raise HTTPException(status_code=500, detail="Internal server error.")
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error.")


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)