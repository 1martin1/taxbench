import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import List

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator


DB_PATH = "db.sqlite3"
MAX_SERVICE_ID_LENGTH = 255
MAX_TOKEN_LENGTH = 255
SERVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")


def get_app_secret() -> str:
    secret = os.getenv("APP_SECRET")
    if secret:
        return secret
    return "default-insecure-secret-change-me"


def hash_token(token: str) -> str:
    secret = get_app_secret().encode("utf-8")
    return hmac.new(secret, token.encode("utf-8"), hashlib.sha256).hexdigest()


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
                service_id TEXT PRIMARY KEY,
                token_hash TEXT NOT NULL,
                last_notification TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_service_heartbeats_token_hash ON service_heartbeats(token_hash)"
        )

        existing_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(service_heartbeats)").fetchall()
        }

        if "token_hash" not in existing_columns or "service_id" not in existing_columns:
            conn.execute("DROP TABLE IF EXISTS service_heartbeats")
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

        conn.commit()


class HeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    serviceId: str = Field(..., description="The unique identifier of the service.")
    token: str = Field(
        ...,
        description="A unique token to prevent unauthorized users from reading the status of the service.",
    )

    @field_validator("serviceId")
    @classmethod
    def validate_service_id(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("serviceId must be a string")
        if not value or not value.strip():
            raise ValueError("serviceId must not be empty")
        value = value.strip()
        if len(value) > MAX_SERVICE_ID_LENGTH:
            raise ValueError(f"serviceId must be at most {MAX_SERVICE_ID_LENGTH} characters long")
        if not SERVICE_ID_PATTERN.fullmatch(value):
            raise ValueError("serviceId contains invalid characters")
        return value

    @field_validator("token")
    @classmethod
    def validate_token(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("token must be a string")
        if not value or not value.strip():
            raise ValueError("token must not be empty")
        if len(value) > MAX_TOKEN_LENGTH:
            raise ValueError(f"token must be at most {MAX_TOKEN_LENGTH} characters long")
        return value


class ServicesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(
        ..., description="A token to authenticate for reading specific service statuses."
    )

    @field_validator("token")
    @classmethod
    def validate_token(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("token must be a string")
        if not value or not value.strip():
            raise ValueError("token must not be empty")
        if len(value) > MAX_TOKEN_LENGTH:
            raise ValueError(f"token must be at most {MAX_TOKEN_LENGTH} characters long")
        return value


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
        token_hash = hash_token(payload.token)

        with closing(get_db_connection()) as conn:
            existing = conn.execute(
                """
                SELECT token_hash
                FROM service_heartbeats
                WHERE service_id = ?
                """,
                (payload.serviceId,),
            ).fetchone()

            if existing is not None and not secrets.compare_digest(
                existing["token_hash"], token_hash
            ):
                raise HTTPException(status_code=400, detail="Invalid request payload.")

            conn.execute(
                """
                INSERT INTO service_heartbeats (service_id, token_hash, last_notification)
                VALUES (?, ?, ?)
                ON CONFLICT(service_id)
                DO UPDATE SET
                    token_hash = excluded.token_hash,
                    last_notification = excluded.last_notification
                """,
                (payload.serviceId, token_hash, now),
            )
            conn.commit()

        return {"message": "Heartbeat registered successfully."}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Internal server error.") from exc


@app.post("/services", response_model=List[ServiceStatusResponse], status_code=200)
def get_services_overview(payload: ServicesRequest):
    try:
        token_hash = hash_token(payload.token)

        with closing(get_db_connection()) as conn:
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
                lastNotification=row["last_notification"],
            )
            for row in rows
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Internal server error.") from exc


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)