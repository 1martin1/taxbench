import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator, List

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field


DB_PATH = "db.sqlite3"
MAX_SERVICE_ID_LENGTH = 255
MAX_TOKEN_LENGTH = 255
MAX_SERVICES_PER_TOKEN = 1000


def _get_secret_key() -> bytes:
    secret = os.getenv("APP_SECRET")
    if secret:
        return secret.encode("utf-8")
    return b"service-monitoring-default-secret"


SECRET_KEY = _get_secret_key()


app = FastAPI(
    title="Service Monitoring API",
    version="1.0.0",
    description="API for monitoring the status of services.",
)


class HeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    serviceId: str = Field(
        ...,
        description="The unique identifier of the service.",
        min_length=1,
        max_length=MAX_SERVICE_ID_LENGTH,
    )
    token: str = Field(
        ...,
        description="A unique token to prevent unauthorized users from reading the status of the service.",
        min_length=1,
        max_length=MAX_TOKEN_LENGTH,
    )


class ServicesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(
        ...,
        description="A token to authenticate for reading specific service statuses.",
        min_length=1,
        max_length=MAX_TOKEN_LENGTH,
    )


class ServiceStatus(BaseModel):
    serviceId: str = Field(..., description="The unique identifier of the service.")
    lastNotification: datetime = Field(
        ..., description="The last time a heartbeat was received for the service."
    )


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def hash_token(token: str) -> str:
    digest = hmac.new(SECRET_KEY, token.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def init_db() -> None:
    with get_db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS service_heartbeats (
                service_id TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                last_notification TEXT NOT NULL,
                PRIMARY KEY (service_id, token_hash)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_service_heartbeats_token_hash
            ON service_heartbeats (token_hash)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS token_service_counts (
                token_hash TEXT PRIMARY KEY,
                service_count INTEGER NOT NULL CHECK (service_count >= 0)
            )
            """
        )

        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(service_heartbeats)").fetchall()
        }
        if "token" in columns and "token_hash" not in columns:
            conn.execute("ALTER TABLE service_heartbeats RENAME TO service_heartbeats_legacy")
            conn.execute(
                """
                CREATE TABLE service_heartbeats (
                    service_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL,
                    last_notification TEXT NOT NULL,
                    PRIMARY KEY (service_id, token_hash)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_service_heartbeats_token_hash
                ON service_heartbeats (token_hash)
                """
            )
            legacy_rows = conn.execute(
                """
                SELECT service_id, token, last_notification
                FROM service_heartbeats_legacy
                """
            ).fetchall()
            counts: dict[str, int] = {}
            for row in legacy_rows:
                token_hash = hash_token(row["token"])
                conn.execute(
                    """
                    INSERT OR REPLACE INTO service_heartbeats (service_id, token_hash, last_notification)
                    VALUES (?, ?, ?)
                    """,
                    (row["service_id"], token_hash, row["last_notification"]),
                )
                counts[token_hash] = counts.get(token_hash, 0) + 1

            for token_hash, count in counts.items():
                conn.execute(
                    """
                    INSERT OR REPLACE INTO token_service_counts (token_hash, service_count)
                    VALUES (?, ?)
                    """,
                    (token_hash, count),
                )

            conn.execute("DROP TABLE service_heartbeats_legacy")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"detail": "Invalid request payload."},
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
def register_heartbeat(payload: HeartbeatRequest) -> Response:
    service_id = payload.serviceId.strip()
    token = payload.token.strip()

    if not service_id or not token:
        raise HTTPException(status_code=400, detail="Invalid request payload.")

    if len(service_id) > MAX_SERVICE_ID_LENGTH or len(token) > MAX_TOKEN_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid request payload.")

    token_hash = hash_token(token)
    now = datetime.now(timezone.utc).isoformat()

    try:
        with get_db() as conn:
            existing = conn.execute(
                """
                SELECT 1
                FROM service_heartbeats
                WHERE service_id = ? AND token_hash = ?
                """,
                (service_id, token_hash),
            ).fetchone()

            if existing is None:
                count_row = conn.execute(
                    """
                    SELECT service_count
                    FROM token_service_counts
                    WHERE token_hash = ?
                    """,
                    (token_hash,),
                ).fetchone()
                current_count = count_row["service_count"] if count_row else 0
                if current_count >= MAX_SERVICES_PER_TOKEN:
                    raise HTTPException(status_code=500, detail="Internal server error.")

                conn.execute(
                    """
                    INSERT INTO service_heartbeats (service_id, token_hash, last_notification)
                    VALUES (?, ?, ?)
                    """,
                    (service_id, token_hash, now),
                )
                conn.execute(
                    """
                    INSERT INTO token_service_counts (token_hash, service_count)
                    VALUES (?, 1)
                    ON CONFLICT(token_hash)
                    DO UPDATE SET service_count = service_count + 1
                    """,
                    (token_hash,),
                )
            else:
                conn.execute(
                    """
                    UPDATE service_heartbeats
                    SET last_notification = ?
                    WHERE service_id = ? AND token_hash = ?
                    """,
                    (now, service_id, token_hash),
                )

        return Response(status_code=200)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error.")


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
    token = payload.token.strip()

    if not token:
        return []

    if len(token) > MAX_TOKEN_LENGTH:
        raise HTTPException(status_code=500, detail="Internal server error.")

    token_hash = hash_token(token)

    try:
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT service_id, last_notification
                FROM service_heartbeats
                WHERE token_hash = ?
                ORDER BY service_id ASC
                LIMIT ?
                """,
                (token_hash, MAX_SERVICES_PER_TOKEN),
            ).fetchall()

        return [
            ServiceStatus(
                serviceId=row["service_id"],
                lastNotification=datetime.fromisoformat(row["last_notification"]),
            )
            for row in rows
        ]
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error.")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)