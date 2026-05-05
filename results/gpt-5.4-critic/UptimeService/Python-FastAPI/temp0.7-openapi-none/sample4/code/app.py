import hashlib
import hmac
import os
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field


DB_PATH = "db.sqlite3"
APP_SECRET = os.environ.get("APP_SECRET", "")
MAX_BODY_SIZE = 4096
MAX_SERVICE_ID_LENGTH = 255
MAX_TOKEN_LENGTH = 255
MAX_ROWS = 10000
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 120
DB_BUSY_TIMEOUT_MS = 5000

_rate_limit_lock = threading.Lock()
_rate_limit_store = {}
_db_write_lock = threading.Lock()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        first = forwarded_for.split(",")[0].strip()
        if first:
            return first
    client = request.client
    return client.host if client and client.host else "unknown"


def rate_limit_key(request: Request) -> str:
    return f"{get_client_ip(request)}:{request.url.path}"


def check_rate_limit(request: Request) -> None:
    now = time.monotonic()
    key = rate_limit_key(request)
    with _rate_limit_lock:
        entry = _rate_limit_store.get(key)
        if entry is None or now - entry["start"] >= RATE_LIMIT_WINDOW_SECONDS:
            _rate_limit_store[key] = {"start": now, "count": 1}
            return
        entry["count"] += 1
        if entry["count"] > RATE_LIMIT_MAX_REQUESTS:
            raise HTTPException(status_code=429, detail="Too many requests.")


def derive_token_hash(token: str) -> str:
    secret = APP_SECRET.encode("utf-8")
    token_bytes = token.encode("utf-8")
    return hmac.new(secret, token_bytes, hashlib.sha256).hexdigest()


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=DB_BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}")
    return conn


def init_db() -> None:
    with closing(get_db_connection()) as conn:
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
            "CREATE INDEX IF NOT EXISTS idx_service_heartbeats_token_hash ON service_heartbeats(token_hash)"
        )
        conn.commit()

        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(service_heartbeats)").fetchall()
        }
        if "token" in columns and "token_hash" not in columns:
            raise RuntimeError("Unsupported legacy database schema detected.")
        if "token" in columns:
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
            legacy_rows = conn.execute(
                "SELECT service_id, token, last_notification FROM service_heartbeats_legacy"
            ).fetchall()
            for row in legacy_rows:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO service_heartbeats (service_id, token_hash, last_notification)
                    VALUES (?, ?, ?)
                    """,
                    (
                        row["service_id"],
                        derive_token_hash(row["token"]),
                        row["last_notification"],
                    ),
                )
            conn.execute("DROP TABLE service_heartbeats_legacy")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_service_heartbeats_token_hash ON service_heartbeats(token_hash)"
            )
            conn.commit()


def validate_text_field(value: Optional[str], field_name: str, max_length: int) -> str:
    if value is None:
        raise HTTPException(status_code=400, detail=f"{field_name} is required.")
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{field_name} must be a string.")
    if len(value) == 0:
        raise HTTPException(status_code=400, detail=f"{field_name} must not be empty.")
    if len(value) > max_length:
        raise HTTPException(status_code=400, detail=f"{field_name} is too long.")
    return value


class HeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    serviceId: Optional[str] = Field(
        default=None,
        description="The unique identifier of the service.",
        max_length=MAX_SERVICE_ID_LENGTH,
    )
    token: Optional[str] = Field(
        default=None,
        description="A unique token to prevent unauthorized users from reading the status of the service.",
        max_length=MAX_TOKEN_LENGTH,
    )


class ServicesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: Optional[str] = Field(
        default=None,
        description="A token to authenticate for reading specific service statuses.",
        max_length=MAX_TOKEN_LENGTH,
    )


class ServiceStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    serviceId: str
    lastNotification: datetime


app = FastAPI(
    title="Service Monitoring API",
    version="1.0.0",
    description="API for monitoring the status of services.",
)


@app.middleware("http")
async def enforce_request_limits(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_BODY_SIZE:
                return Response(status_code=413)
        except ValueError:
            return Response(status_code=400)
    check_rate_limit(request)
    return await call_next(request)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.post("/heartbeat", status_code=200, response_class=Response)
def register_heartbeat(payload: HeartbeatRequest):
    service_id = validate_text_field(payload.serviceId, "serviceId", MAX_SERVICE_ID_LENGTH)
    token = validate_text_field(payload.token, "token", MAX_TOKEN_LENGTH)
    token_hash = derive_token_hash(token)
    now = utcnow().isoformat()

    try:
        with _db_write_lock:
            with closing(get_db_connection()) as conn:
                current_count_row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM service_heartbeats"
                ).fetchone()
                current_count = int(current_count_row["cnt"]) if current_count_row else 0

                exists_row = conn.execute(
                    """
                    SELECT 1
                    FROM service_heartbeats
                    WHERE service_id = ? AND token_hash = ?
                    LIMIT 1
                    """,
                    (service_id, token_hash),
                ).fetchone()

                if exists_row is None and current_count >= MAX_ROWS:
                    raise HTTPException(status_code=500, detail="Internal server error.")

                conn.execute(
                    """
                    INSERT INTO service_heartbeats (service_id, token_hash, last_notification)
                    VALUES (?, ?, ?)
                    ON CONFLICT(service_id, token_hash)
                    DO UPDATE SET last_notification = excluded.last_notification
                    """,
                    (service_id, token_hash, now),
                )
                conn.commit()
    except HTTPException:
        raise
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Internal server error.")

    return Response(status_code=200)


@app.post("/services", response_model=List[ServiceStatusResponse], status_code=200)
def get_services(payload: ServicesRequest):
    token = validate_text_field(payload.token, "token", MAX_TOKEN_LENGTH)
    token_hash = derive_token_hash(token)

    try:
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
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Internal server error.")

    result = []
    for row in rows:
        result.append(
            ServiceStatusResponse(
                serviceId=row["service_id"],
                lastNotification=datetime.fromisoformat(row["last_notification"]),
            )
        )
    return result


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)