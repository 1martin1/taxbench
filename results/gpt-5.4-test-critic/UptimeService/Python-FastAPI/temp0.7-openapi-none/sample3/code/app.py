import hashlib
import hmac
import os
import sqlite3
import threading
import time
from contextlib import contextmanager, asynccontextmanager
from datetime import datetime, timezone
from typing import List

import uvicorn
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field


DB_PATH = "db.sqlite3"
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_ATTEMPTS = 30


_rate_limit_lock = threading.Lock()
_rate_limit_store = {}


def _get_app_secret() -> str:
    secret = os.environ.get("APP_SECRET")
    if secret:
        return secret
    return "default-app-secret-change-me"


APP_SECRET = _get_app_secret()


def hash_token(token: str) -> str:
    return hmac.new(APP_SECRET.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


def check_rate_limit(token: str) -> None:
    now = time.time()
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    with _rate_limit_lock:
        attempts = _rate_limit_store.get(token_hash, [])
        attempts = [ts for ts in attempts if now - ts < RATE_LIMIT_WINDOW_SECONDS]

        if len(attempts) >= RATE_LIMIT_MAX_ATTEMPTS:
            _rate_limit_store[token_hash] = attempts
            raise HTTPException(status_code=500, detail="Internal server error.")

        attempts.append(now)
        _rate_limit_store[token_hash] = attempts

        if len(_rate_limit_store) > 10000:
            expired_keys = [
                key for key, values in _rate_limit_store.items()
                if not values or now - values[-1] >= RATE_LIMIT_WINDOW_SECONDS
            ]
            for key in expired_keys:
                _rate_limit_store.pop(key, None)


class HeartbeatRequest(BaseModel):
    serviceId: str = Field(..., description="The unique identifier of the service.")
    token: str = Field(
        ...,
        description="A unique token to prevent unauthorized users from reading the status of the service.",
    )


class ServicesRequest(BaseModel):
    token: str = Field(
        ...,
        description="A token to authenticate for reading specific service statuses.",
    )


class ServiceOverview(BaseModel):
    serviceId: str
    lastNotification: datetime

    model_config = ConfigDict(from_attributes=True)


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
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
            """
            CREATE INDEX IF NOT EXISTS idx_services_token_hash
            ON services (token_hash)
            """
        )

        columns = {
            row["name"]: row
            for row in conn.execute("PRAGMA table_info(services)").fetchall()
        }

        if "token" in columns and "token_hash" not in columns:
            conn.execute("ALTER TABLE services ADD COLUMN token_hash TEXT")
            rows = conn.execute(
                "SELECT service_id, token, last_notification FROM services"
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    UPDATE services
                    SET token_hash = ?
                    WHERE service_id = ? AND token = ? AND last_notification = ?
                    """,
                    (
                        hash_token(row["token"]),
                        row["service_id"],
                        row["token"],
                        row["last_notification"],
                    ),
                )

            conn.execute("DROP INDEX IF EXISTS idx_services_token")
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
                INSERT INTO services (service_id, token_hash, last_notification)
                SELECT service_id, token_hash, last_notification
                FROM services_old
                WHERE token_hash IS NOT NULL
                """
            )
            conn.execute("DROP TABLE services_old")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_services_token_hash
                ON services (token_hash)
                """
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Service Monitoring API",
    version="1.0.0",
    description="API for monitoring the status of services.",
    lifespan=lifespan,
)


@app.post("/heartbeat", status_code=200)
def register_heartbeat(payload: HeartbeatRequest):
    service_id = payload.serviceId.strip()
    token = payload.token.strip()

    if not service_id or not token:
        raise HTTPException(status_code=400, detail="serviceId and token must not be empty.")

    now = datetime.now(timezone.utc).isoformat()
    token_digest = hash_token(token)

    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO services (service_id, token_hash, last_notification)
                VALUES (?, ?, ?)
                ON CONFLICT(service_id, token_hash)
                DO UPDATE SET last_notification = excluded.last_notification
                """,
                (service_id, token_digest, now),
            )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error.")

    return Response(status_code=200)


@app.post("/services", response_model=List[ServiceOverview], status_code=200)
def get_services(payload: ServicesRequest):
    token = payload.token.strip()

    if not token:
        raise HTTPException(status_code=400, detail="token must not be empty.")

    check_rate_limit(token)
    token_digest = hash_token(token)

    try:
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT service_id, token_hash, last_notification
                FROM services
                WHERE token_hash = ?
                ORDER BY service_id ASC
                """,
                (token_digest,),
            ).fetchall()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error.")

    result = []
    for row in rows:
        if not constant_time_equals(row["token_hash"], token_digest):
            continue
        result.append(
            ServiceOverview(
                serviceId=row["service_id"],
                lastNotification=datetime.fromisoformat(row["last_notification"]),
            )
        )
    return result


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)