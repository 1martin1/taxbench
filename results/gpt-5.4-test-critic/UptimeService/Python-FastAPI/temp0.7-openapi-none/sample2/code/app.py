import hashlib
import hmac
import os
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from typing import Generator, List

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, field_validator


DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET", "")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_token(token: str) -> str:
    return token.strip()


def _hash_token(token: str) -> str:
    normalized = _normalize_token(token)
    secret = APP_SECRET.encode("utf-8")
    return hmac.new(secret, normalized.encode("utf-8"), hashlib.sha256).hexdigest()


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS service_heartbeats (
                service_id TEXT PRIMARY KEY,
                token_hash TEXT NOT NULL,
                last_notification TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_service_heartbeats_token_hash ON service_heartbeats(token_hash)"
        )

        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(service_heartbeats)").fetchall()
        }

        if "token" in columns:
            rows = conn.execute(
                """
                SELECT service_id, token, last_notification
                FROM service_heartbeats
                """
            ).fetchall()

            conn.execute("ALTER TABLE service_heartbeats RENAME TO service_heartbeats_old")
            conn.execute(
                """
                CREATE TABLE service_heartbeats (
                    service_id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL,
                    last_notification TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_service_heartbeats_token_hash ON service_heartbeats(token_hash)"
            )

            for row in rows:
                service_id = (row["service_id"] or "").strip()
                token = (row["token"] or "").strip()
                last_notification = row["last_notification"]
                if service_id and token and last_notification:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO service_heartbeats (service_id, token_hash, last_notification)
                        VALUES (?, ?, ?)
                        """,
                        (service_id, _hash_token(token), last_notification),
                    )

            conn.execute("DROP TABLE service_heartbeats_old")

        conn.commit()


class HeartbeatRequest(BaseModel):
    serviceId: str
    token: str

    @field_validator("serviceId", "token")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must be a non-empty string")
        return value.strip()


class ServicesRequest(BaseModel):
    token: str

    @field_validator("token")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must be a non-empty string")
        return value.strip()


class ServiceStatusResponse(BaseModel):
    serviceId: str
    lastNotification: datetime

    model_config = ConfigDict(from_attributes=True)


@asynccontextmanager
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
async def validation_exception_handler(_: Request, __: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": "Invalid request payload"})


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
        service_id = payload.serviceId
        token_hash = _hash_token(payload.token)
        now = _utc_now_iso()

        with get_db() as conn:
            existing = conn.execute(
                """
                SELECT token_hash
                FROM service_heartbeats
                WHERE service_id = ?
                """,
                (service_id,),
            ).fetchone()

            if existing is not None and not hmac.compare_digest(existing["token_hash"], token_hash):
                raise HTTPException(status_code=400, detail="Invalid request payload")

            conn.execute(
                """
                INSERT INTO service_heartbeats (service_id, token_hash, last_notification)
                VALUES (?, ?, ?)
                ON CONFLICT(service_id)
                DO UPDATE SET last_notification = excluded.last_notification
                """,
                (service_id, token_hash, now),
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
        400: {"description": "Invalid request payload."},
        500: {"description": "Internal server error."},
    },
)
def get_services(payload: ServicesRequest):
    try:
        token_hash = _hash_token(payload.token)

        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT service_id, last_notification
                FROM service_heartbeats
                WHERE token_hash = ?
                ORDER BY service_id ASC
                """,
                (token_hash,),
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