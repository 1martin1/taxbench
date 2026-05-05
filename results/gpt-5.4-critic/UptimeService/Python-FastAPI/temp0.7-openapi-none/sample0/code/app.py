import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator, List

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator


DB_PATH = "db.sqlite3"
MAX_SERVICE_ID_LENGTH = 128
MIN_TOKEN_LENGTH = 12
MAX_TOKEN_LENGTH = 256
MAX_SERVICES_PER_TOKEN = 1000
MAX_SERVICES_RESPONSE = 1000
TOKEN_ALLOWED_PATTERN = re.compile(r"^[A-Za-z0-9._~+\-=/]+$")
SERVICE_ID_ALLOWED_PATTERN = re.compile(r"^[A-Za-z0-9._:\-]+$")


app = FastAPI(
    title="Service Monitoring API",
    version="1.0.0",
    description="API for monitoring the status of services.",
)


def _get_app_secret() -> str:
    secret = os.getenv("APP_SECRET")
    if not secret:
        secret = "default-local-secret-change-me"
    return secret


def _hash_token(token: str) -> str:
    secret = _get_app_secret().encode("utf-8")
    return hmac.new(secret, token.encode("utf-8"), hashlib.sha256).hexdigest()


def _normalize_datetime(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str)


class HeartbeatRequest(BaseModel):
    serviceId: str = Field(..., description="The unique identifier of the service.")
    token: str = Field(
        ...,
        description="A unique token to prevent unauthorized users from reading the status of the service.",
    )

    @field_validator("serviceId")
    @classmethod
    def validate_service_id(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("serviceId must be a string.")
        value = value.strip()
        if not value:
            raise ValueError("serviceId must not be empty.")
        if len(value) > MAX_SERVICE_ID_LENGTH:
            raise ValueError("serviceId is too long.")
        if not SERVICE_ID_ALLOWED_PATTERN.fullmatch(value):
            raise ValueError("serviceId contains invalid characters.")
        return value

    @field_validator("token")
    @classmethod
    def validate_token(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("token must be a string.")
        if len(value) < MIN_TOKEN_LENGTH:
            raise ValueError("token is too short.")
        if len(value) > MAX_TOKEN_LENGTH:
            raise ValueError("token is too long.")
        if any(ch.isspace() for ch in value):
            raise ValueError("token must not contain whitespace.")
        if not TOKEN_ALLOWED_PATTERN.fullmatch(value):
            raise ValueError("token contains invalid characters.")
        if value.lower() in {"password", "pass1", "token", "secret", "123456789012"}:
            raise ValueError("token is too weak.")
        return value


class ServicesRequest(BaseModel):
    token: str = Field(
        ...,
        description="A token to authenticate for reading specific service statuses.",
    )

    @field_validator("token")
    @classmethod
    def validate_token(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("token must be a string.")
        if len(value) < MIN_TOKEN_LENGTH:
            raise ValueError("token is too short.")
        if len(value) > MAX_TOKEN_LENGTH:
            raise ValueError("token is too long.")
        if any(ch.isspace() for ch in value):
            raise ValueError("token must not contain whitespace.")
        if not TOKEN_ALLOWED_PATTERN.fullmatch(value):
            raise ValueError("token contains invalid characters.")
        if value.lower() in {"password", "pass1", "token", "secret", "123456789012"}:
            raise ValueError("token is too weak.")
        return value


class ServiceStatusResponse(BaseModel):
    serviceId: str
    lastNotification: datetime

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "serviceId": "my-service",
                "lastNotification": "2025-01-01T12:00:00Z",
            }
        }
    )


@contextmanager
def get_db_connection() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with get_db_connection() as conn:
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
            ON service_heartbeats (token_hash, service_id)
            """
        )
        conn.commit()


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    status_code = 400 if request.url.path == "/heartbeat" else 500 if request.url.path == "/services" else 400
    return JSONResponse(
        status_code=status_code,
        content={"detail": "Invalid request payload." if status_code == 400 else "Internal server error."},
    )


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.post("/heartbeat", status_code=200)
def register_heartbeat(payload: HeartbeatRequest) -> dict[str, str]:
    try:
        now = datetime.now(timezone.utc).isoformat()
        token_hash = _hash_token(payload.token)

        with get_db_connection() as conn:
            existing = conn.execute(
                """
                SELECT 1
                FROM service_heartbeats
                WHERE service_id = ? AND token_hash = ?
                LIMIT 1
                """,
                (payload.serviceId, token_hash),
            ).fetchone()

            if existing is None:
                count_row = conn.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM service_heartbeats
                    WHERE token_hash = ?
                    """,
                    (token_hash,),
                ).fetchone()
                current_count = int(count_row["cnt"]) if count_row is not None else 0
                if current_count >= MAX_SERVICES_PER_TOKEN:
                    raise HTTPException(status_code=500, detail="Internal server error.")

            conn.execute(
                """
                INSERT INTO service_heartbeats (service_id, token_hash, last_notification)
                VALUES (?, ?, ?)
                ON CONFLICT(service_id, token_hash)
                DO UPDATE SET last_notification = excluded.last_notification
                """,
                (payload.serviceId, token_hash, now),
            )
            conn.commit()

        return {"message": "Heartbeat registered successfully."}
    except HTTPException:
        raise
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail="Internal server error.") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Internal server error.") from exc


@app.post("/services", response_model=List[ServiceStatusResponse], status_code=200)
def get_services_overview(payload: ServicesRequest) -> List[ServiceStatusResponse]:
    try:
        token_hash = _hash_token(payload.token)

        with get_db_connection() as conn:
            rows = conn.execute(
                """
                SELECT service_id, last_notification
                FROM service_heartbeats
                WHERE token_hash = ?
                ORDER BY service_id ASC
                LIMIT ?
                """,
                (token_hash, MAX_SERVICES_RESPONSE),
            ).fetchall()

        services = [
            ServiceStatusResponse(
                serviceId=row["service_id"],
                lastNotification=_normalize_datetime(row["last_notification"]),
            )
            for row in rows
        ]
        return services
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail="Internal server error.") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Internal server error.") from exc


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)