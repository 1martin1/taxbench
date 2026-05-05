import os
import datetime
import hashlib
import sqlite3
import threading
import time
from typing import List, Tuple, Dict

from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, constr
from starlette.concurrency import run_in_threadpool

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DATABASE = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET", "")

# ---------------------------------------------------------------------------
# Security utilities
# ---------------------------------------------------------------------------
def hash_token(token: str) -> str:
    """Return a SHA256 hash of the token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
_RATE_LIMIT = 30  # requests
_RATE_PERIOD = 60  # seconds
_rate_lock = threading.Lock()
_rate_store: Dict[Tuple[str, str], List[float]] = {}

def check_rate_limit(request: Request, endpoint: str):
    client_ip = request.client.host if request.client else "unknown"
    key = (client_ip, endpoint)
    now = time.time()
    with _rate_lock:
        timestamps = _rate_store.get(key, [])
        # Remove timestamps older than the period
        timestamps = [ts for ts in timestamps if now - ts < _RATE_PERIOD]
        if len(timestamps) >= _RATE_LIMIT:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
            )
        timestamps.append(now)
        _rate_store[key] = timestamps

# ---------------------------------------------------------------------------
# Request size limiting middleware
# ---------------------------------------------------------------------------
_MAX_CONTENT_LENGTH = 4 * 1024  # 4 KiB

class ContentLengthLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > _MAX_CONTENT_LENGTH:
                    return JSONResponse(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        content={"detail": "Request body too large"},
                    )
            except ValueError:
                pass  # ignore malformed header, let downstream handle it
        return await call_next(request)

# ---------------------------------------------------------------------------
# Pydantic models matching the OpenAPI schema
# ---------------------------------------------------------------------------
class HeartbeatRequest(BaseModel):
    serviceId: constr(max_length=128)
    token: constr(max_length=128)


class ServicesRequest(BaseModel):
    token: constr(max_length=128)


class ServiceInfo(BaseModel):
    serviceId: str
    lastNotification: datetime.datetime

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Service Monitoring API",
    version="1.0.0",
    description="API for monitoring the status of services.",
    # Use the middleware for request size limiting
    middleware=[{"middleware_class": ContentLengthLimitMiddleware}]
)

# ---------------------------------------------------------------------------
# Database utilities
# ---------------------------------------------------------------------------
def get_db():
    """Yield a SQLite connection for each request (thread‑safe)."""
    conn = sqlite3.connect(
        DATABASE,
        detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        check_same_thread=False,  # allow usage from other threads (run_in_threadpool)
    )
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


@app.on_event("startup")
def on_startup():
    """Create the required table if it does not exist."""
    with sqlite3.connect(DATABASE, check_same_thread=False) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_id TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                last_notification TEXT NOT NULL,
                UNIQUE(service_id, token_hash)
            )
            """
        )
        conn.commit()

# ---------------------------------------------------------------------------
# Error handling – map validation errors to 400 as required by the spec
# ---------------------------------------------------------------------------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": exc.errors()},
    )

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.post(
    "/heartbeat",
    status_code=200,
    responses={
        200: {"description": "Heartbeat registered successfully."},
        400: {"description": "Invalid request payload."},
        429: {"description": "Rate limit exceeded."},
        500: {"description": "Internal server error."},
    },
)
async def register_heartbeat(
    payload: HeartbeatRequest,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Register (or update) a heartbeat for a given service.
    """
    check_rate_limit(request, "heartbeat")
    token_hash = hash_token(payload.token)
    now_iso = datetime.datetime.utcnow().isoformat(timespec="seconds")
    try:
        await run_in_threadpool(
            db.execute,
            """
            INSERT INTO services (service_id, token_hash, last_notification)
            VALUES (?, ?, ?)
            ON CONFLICT(service_id, token_hash) DO UPDATE SET last_notification=excluded.last_notification
            """,
            (payload.serviceId, token_hash, now_iso),
        )
        await run_in_threadpool(db.commit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    return {"detail": "Heartbeat registered successfully."}


@app.post(
    "/services",
    response_model=List[ServiceInfo],
    responses={
        200: {"description": "A list of registered services with their last notification date."},
        429: {"description": "Rate limit exceeded."},
        500: {"description": "Internal server error."},
    },
)
async def get_services(
    payload: ServicesRequest,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Return services that were registered with the provided token.
    The result set is capped to avoid excessive memory usage.
    """
    check_rate_limit(request, "services")
    token_hash = hash_token(payload.token)
    try:
        cursor = await run_in_threadpool(
            db.execute,
            "SELECT service_id, last_notification FROM services WHERE token_hash = ? LIMIT 1000",
            (token_hash,),
        )
        rows = cursor.fetchall()
        result = [
            ServiceInfo(
                serviceId=row["service_id"],
                lastNotification=datetime.datetime.fromisoformat(row["last_notification"]),
            )
            for row in rows
        ]
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Internal server error") from exc


# ---------------------------------------------------------------------------
# Run the application
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)