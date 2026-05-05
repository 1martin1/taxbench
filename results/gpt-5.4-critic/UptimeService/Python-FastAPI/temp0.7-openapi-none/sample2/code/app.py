import hashlib
import hmac
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Dict, List, Optional

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET", "default-app-secret")

MAX_SERVICE_ID_LENGTH = 255
MAX_TOKEN_LENGTH = 255
MAX_SERVICES_PER_TOKEN = 1000

RATE_LIMIT_WINDOW_SECONDS = 60
HEARTBEAT_RATE_LIMIT = 120
SERVICES_RATE_LIMIT = 120

_db_available = False
_db_init_error: Optional[str] = None
_rate_limit_lock = threading.Lock()
_rate_limit_store: Dict[str, List[float]] = {}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def hash_token(token: str) -> str:
    return hmac.new(APP_SECRET.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def normalize_datetime(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def cleanup_rate_limit_store(now: float) -> None:
    expired_keys = []
    for key, timestamps in _rate_limit_store.items():
        filtered = [ts for ts in timestamps if now - ts < RATE_LIMIT_WINDOW_SECONDS]
        if filtered:
            _rate_limit_store[key] = filtered
        else:
            expired_keys.append(key)
    for key in expired_keys:
        _rate_limit_store.pop(key, None)


def check_rate_limit(bucket: str, limit: int) -> bool:
    now = time.time()
    with _rate_limit_lock:
        cleanup_rate_limit_store(now)
        timestamps = _rate_limit_store.get(bucket, [])
        timestamps = [ts for ts in timestamps if now - ts < RATE_LIMIT_WINDOW_SECONDS]
        if len(timestamps) >= limit:
            _rate_limit_store[bucket] = timestamps
            return False
        timestamps.append(now)
        _rate_limit_store[bucket] = timestamps
        return True


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=5.0, check_same_thread=False)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    global _db_available, _db_init_error
    try:
        with get_db() as conn:
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

            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(services)").fetchall()
            }
            if "token" in columns and "token_hash" not in columns:
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
                old_rows = conn.execute(
                    "SELECT service_id, token, last_notification FROM services_old"
                ).fetchall()
                for row in old_rows:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO services (service_id, token_hash, last_notification)
                        VALUES (?, ?, ?)
                        """,
                        (
                            row["service_id"],
                            hash_token(row["token"]),
                            row["last_notification"],
                        ),
                    )
                conn.execute("DROP TABLE services_old")
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_services_token_hash ON services(token_hash)"
                )

        _db_available = True
        _db_init_error = None
    except Exception as exc:
        _db_available = False
        _db_init_error = str(exc)


class HeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    serviceId: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=MAX_SERVICE_ID_LENGTH,
        description="The unique identifier of the service.",
    )
    token: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=MAX_TOKEN_LENGTH,
        description="A unique token to prevent unauthorized users from reading the status of the service.",
    )


class ServicesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=MAX_TOKEN_LENGTH,
        description="A token to authenticate for reading specific service statuses.",
    )


class ServiceStatus(BaseModel):
    serviceId: str
    lastNotification: datetime


app = FastAPI(
    title="Service Monitoring API",
    version="1.0.0",
    description="API for monitoring the status of services.",
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={"detail": "Invalid request payload."},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error."},
    )


@app.middleware("http")
async def enforce_rate_limits(request: Request, call_next):
    client_host = request.client.host if request.client else "unknown"
    path = request.url.path

    if path == "/heartbeat":
        allowed = check_rate_limit(f"heartbeat:{client_host}", HEARTBEAT_RATE_LIMIT)
        if not allowed:
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal server error."},
            )
    elif path == "/services":
        allowed = check_rate_limit(f"services:{client_host}", SERVICES_RATE_LIMIT)
        if not allowed:
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal server error."},
            )

    return await call_next(request)


def ensure_db_available() -> None:
    if not _db_available:
        raise RuntimeError(_db_init_error or "Database unavailable")


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
    if payload.serviceId is None or payload.token is None:
        return JSONResponse(
            status_code=400,
            content={"detail": "Invalid request payload."},
        )

    ensure_db_available()

    token_hash = hash_token(payload.token)
    now = utc_now_iso()

    with get_db() as conn:
        existing = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM services
            WHERE token_hash = ?
            """,
            (token_hash,),
        ).fetchone()

        current_count = int(existing["cnt"]) if existing is not None else 0

        row = conn.execute(
            """
            SELECT 1
            FROM services
            WHERE service_id = ? AND token_hash = ?
            LIMIT 1
            """,
            (payload.serviceId, token_hash),
        ).fetchone()

        if row is None and current_count >= MAX_SERVICES_PER_TOKEN:
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal server error."},
            )

        conn.execute(
            """
            INSERT INTO services (service_id, token_hash, last_notification)
            VALUES (?, ?, ?)
            ON CONFLICT(service_id, token_hash)
            DO UPDATE SET last_notification = excluded.last_notification
            """,
            (payload.serviceId, token_hash, now),
        )

    return {"message": "Heartbeat registered successfully."}


@app.post(
    "/services",
    response_model=List[ServiceStatus],
    responses={
        200: {
            "description": "A list of registered services with their last notification date (shows all services registered with the given token)."
        },
        500: {"description": "Internal server error."},
    },
)
def get_services(payload: ServicesRequest):
    if payload.token is None:
        return JSONResponse(
            status_code=400,
            content={"detail": "Invalid request payload."},
        )

    ensure_db_available()

    token_hash = hash_token(payload.token)

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT service_id, last_notification
            FROM services
            WHERE token_hash = ?
            ORDER BY service_id ASC
            """,
            (token_hash,),
        ).fetchall()

    return [
        ServiceStatus(
            serviceId=row["service_id"],
            lastNotification=normalize_datetime(row["last_notification"]),
        )
        for row in rows
    ]


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)