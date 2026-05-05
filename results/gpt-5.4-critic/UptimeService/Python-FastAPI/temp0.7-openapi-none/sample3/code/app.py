import base64
import hashlib
import hmac
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool


DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET", "")
TOKEN_HASH_ITERATIONS = 200_000
SERVICE_ID_MAX_LENGTH = 255
TOKEN_MAX_LENGTH = 255
MAX_SERVICES_RETURNED = 1000
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 120


_rate_limit_lock = threading.Lock()
_rate_limit_store: dict[str, list[float]] = {}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def enforce_rate_limit(client_key: str) -> None:
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    with _rate_limit_lock:
        entries = _rate_limit_store.get(client_key, [])
        entries = [ts for ts in entries if ts >= cutoff]
        if len(entries) >= RATE_LIMIT_MAX_REQUESTS:
            raise HTTPException(status_code=429, detail="Too many requests.")
        entries.append(now)
        _rate_limit_store[client_key] = entries


def derive_token_hash(token: str) -> str:
    secret = APP_SECRET.encode("utf-8")
    token_bytes = token.encode("utf-8")
    digest = hashlib.pbkdf2_hmac(
        "sha256", token_bytes, secret, TOKEN_HASH_ITERATIONS
    )
    return base64.b64encode(digest).decode("ascii")


def constant_time_token_match(token: str, token_hash: str) -> bool:
    expected = derive_token_hash(token)
    return hmac.compare_digest(expected, token_hash)


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS services (
                service_id TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                last_notification TEXT NOT NULL,
                PRIMARY KEY (service_id, token_hash)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_services_token_hash ON services(token_hash)"
        )

        cursor = conn.execute("PRAGMA table_info(services)")
        columns = [row[1] for row in cursor.fetchall()]
        if "token" in columns and "token_hash" not in columns:
            conn.execute("ALTER TABLE services ADD COLUMN token_hash TEXT")
            rows = conn.execute(
                "SELECT rowid, token FROM services WHERE token_hash IS NULL"
            ).fetchall()
            for rowid, token in rows:
                conn.execute(
                    "UPDATE services SET token_hash = ? WHERE rowid = ?",
                    (derive_token_hash(token), rowid),
                )
            conn.commit()

            conn.execute("ALTER TABLE services RENAME TO services_old")
            conn.execute(
                """
                CREATE TABLE services (
                    service_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL,
                    last_notification TEXT NOT NULL,
                    PRIMARY KEY (service_id, token_hash)
                )
                """
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO services (service_id, token_hash, last_notification)
                SELECT service_id, token_hash, last_notification
                FROM services_old
                """
            )
            conn.execute("DROP TABLE services_old")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_services_token_hash ON services(token_hash)"
            )

        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class HeartbeatRequest(BaseModel):
    serviceId: str = Field(min_length=1, max_length=SERVICE_ID_MAX_LENGTH)
    token: str = Field(min_length=1, max_length=TOKEN_MAX_LENGTH)
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "serviceId": "my-service",
                "token": "pass1",
            }
        },
    )


class ServicesRequest(BaseModel):
    token: str = Field(min_length=1, max_length=TOKEN_MAX_LENGTH)
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "token": "pass1",
            }
        },
    )


class ServiceStatus(BaseModel):
    serviceId: str
    lastNotification: AwareDatetime


app = FastAPI(
    title="Service Monitoring API",
    version="1.0.0",
    description="API for monitoring the status of services.",
    openapi_version="3.0.3",
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"detail": "Invalid request payload."},
    )


@app.exception_handler(sqlite3.Error)
async def sqlite_exception_handler(
    request: Request, exc: sqlite3.Error
) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error."},
    )


def register_heartbeat_db(payload: HeartbeatRequest) -> None:
    timestamp = utc_now_iso()
    token_hash = derive_token_hash(payload.token)
    with get_db() as conn:
        existing = conn.execute(
            """
            SELECT token_hash
            FROM services
            WHERE service_id = ?
            LIMIT 1
            """,
            (payload.serviceId,),
        ).fetchone()

        if existing is not None and not constant_time_token_match(
            payload.token, existing["token_hash"]
        ):
            raise HTTPException(status_code=400, detail="Invalid request payload.")

        conn.execute(
            """
            INSERT INTO services (service_id, token_hash, last_notification)
            VALUES (?, ?, ?)
            ON CONFLICT(service_id, token_hash)
            DO UPDATE SET last_notification=excluded.last_notification
            """,
            (payload.serviceId, token_hash, timestamp),
        )


def get_services_db(payload: ServicesRequest) -> list[dict[str, Any]]:
    token_hash = derive_token_hash(payload.token)
    with get_db() as conn:
        cursor = conn.execute(
            """
            SELECT service_id, last_notification
            FROM services
            WHERE token_hash = ?
            ORDER BY service_id ASC
            LIMIT ?
            """,
            (token_hash, MAX_SERVICES_RETURNED),
        )
        rows = cursor.fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        results.append(
            {
                "serviceId": row["service_id"],
                "lastNotification": row["last_notification"],
            }
        )
    return results


@app.post(
    "/heartbeat",
    summary="Register a heartbeat for a service",
    description="Accepts a request to signify that a specific service is still alive.",
    responses={
        200: {"description": "Heartbeat registered successfully."},
        400: {"description": "Invalid request payload."},
        500: {"description": "Internal server error."},
    },
)
async def register_heartbeat(request: Request, payload: HeartbeatRequest) -> Response:
    enforce_rate_limit(f"heartbeat:{get_client_ip(request)}")
    await run_in_threadpool(register_heartbeat_db, payload)
    return Response(status_code=200)


@app.post(
    "/services",
    summary="Get overview of registered services",
    description=(
        "Provides an overview of registered services and their last notification date, "
        "where the token of this request matches the token sent with the service request."
    ),
    response_model=list[ServiceStatus],
    responses={
        200: {
            "description": (
                "A list of registered services with their last notification date "
                "(shows all services registered with the given token)."
            )
        },
        500: {"description": "Internal server error."},
    },
)
async def get_services(request: Request, payload: ServicesRequest) -> list[dict[str, Any]]:
    enforce_rate_limit(f"services:{get_client_ip(request)}")
    return await run_in_threadpool(get_services_db, payload)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)