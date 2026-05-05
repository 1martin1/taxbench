import os
import sqlite3
import hashlib
import hmac
import asyncio
from datetime import datetime, timezone, timedelta
from typing import List, Dict

from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator

# ---------- Configuration ----------
DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET", "default_secret")  # should be set in env
MAX_TOKEN_LENGTH = 128
MAX_SERVICE_ID_LENGTH = 256
RATE_LIMIT = 60  # requests
RATE_LIMIT_WINDOW = 60  # seconds
RETENTION_DAYS = 90  # days to keep heartbeat records

# ---------- Helper Functions ----------
def hash_token(token: str) -> str:
    """Create a deterministic HMAC‑SHA256 hash of the token using the app secret."""
    return hmac.new(APP_SECRET.encode(), token.encode(), hashlib.sha256).hexdigest()


# ---------- Rate Limiting ----------
class RateLimiter:
    def __init__(self):
        self._counts: Dict[str, List[float]] = {}
        self._lock = asyncio.Lock()

    async def check(self, request: Request):
        client_ip = request.client.host if request.client else "unknown"
        now = asyncio.get_event_loop().time()
        async with self._lock:
            timestamps = self._counts.get(client_ip, [])
            # Remove timestamps outside the window
            timestamps = [ts for ts in timestamps if now - ts < RATE_LIMIT_WINDOW]
            if len(timestamps) >= RATE_LIMIT:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many requests, please try again later.",
                )
            timestamps.append(now)
            self._counts[client_ip] = timestamps


rate_limiter = RateLimiter()


# ---------- Pydantic models ----------
class HeartbeatRequest(BaseModel):
    serviceId: str = Field(..., description="The unique identifier of the service.")
    token: str = Field(..., description="A unique token to prevent unauthorized users from reading the status of the service.")

    @validator("serviceId")
    def service_id_length(cls, v):
        if not (1 <= len(v) <= MAX_SERVICE_ID_LENGTH):
            raise ValueError(f"serviceId length must be between 1 and {MAX_SERVICE_ID_LENGTH}")
        return v

    @validator("token")
    def token_length(cls, v):
        if not (1 <= len(v) <= MAX_TOKEN_LENGTH):
            raise ValueError(f"token length must be between 1 and {MAX_TOKEN_LENGTH}")
        return v


class ServicesRequest(BaseModel):
    token: str = Field(..., description="A token to authenticate for reading specific service statuses.")

    @validator("token")
    def token_length(cls, v):
        if not (1 <= len(v) <= MAX_TOKEN_LENGTH):
            raise ValueError(f"token length must be between 1 and {MAX_TOKEN_LENGTH}")
        return v


class ServiceInfo(BaseModel):
    serviceId: str = Field(..., description="The unique identifier of the service.")
    lastNotification: datetime = Field(..., description="The last time a heartbeat was received for the service.")


# ---------- FastAPI app ----------
app = FastAPI(
    title="Service Monitoring API",
    version="1.0.0",
    description="API for monitoring the status of services."
)


# ---------- Database dependency ----------
def get_db():
    """
    Provides a fresh SQLite connection per request.
    """
    conn = sqlite3.connect(
        DB_PATH,
        detect_types=sqlite3.PARSE_DECLTYPES,
        timeout=30,
    )
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ---------- Startup / Cleanup ----------
@app.on_event("startup")
def startup():
    # Initialise DB
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS heartbeats (
                service_id TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                last_notification TEXT NOT NULL,
                PRIMARY KEY (service_id, token_hash)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()
    # Start background cleanup task
    asyncio.create_task(_periodic_cleanup())


async def _periodic_cleanup():
    """Delete heartbeat records older than RETENTION_DAYS, runs daily."""
    while True:
        await asyncio.sleep(24 * 60 * 60)  # 24 hours
        cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM heartbeats WHERE last_notification < ?",
                (cutoff,),
            )
            conn.commit()
        except Exception:
            pass  # In production log the exception
        finally:
            conn.close()


# ---------- Exception Handlers ----------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": exc.errors()},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error."},
    )


# ---------- Endpoints ----------
@app.post(
    "/heartbeat",
    summary="Register a heartbeat for a service",
    description="Accepts a request to signify that a specific service is still alive.",
)
async def register_heartbeat(
    payload: HeartbeatRequest,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
):
    await rate_limiter.check(request)
    token_hash = hash_token(payload.token)
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        cursor = db.cursor()
        cursor.execute(
            """
            INSERT INTO heartbeats (service_id, token_hash, last_notification)
            VALUES (?, ?, ?)
            ON CONFLICT(service_id, token_hash) DO UPDATE SET last_notification=excluded.last_notification
            """,
            (payload.serviceId, token_hash, now_iso),
        )
        db.commit()
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Internal server error.") from e
    return {"detail": "Heartbeat registered successfully."}


@app.post(
    "/services",
    summary="Get overview of registered services",
    description=(
        "Provides an overview of registered services and their last notification date, "
        "where the token of this request matches the token sent with the service request."
    ),
    response_model=List[ServiceInfo],
)
async def list_services(
    payload: ServicesRequest,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
):
    await rate_limiter.check(request)
    token_hash = hash_token(payload.token)
    try:
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT service_id, last_notification
            FROM heartbeats
            WHERE token_hash = ?
            ORDER BY service_id ASC
            """,
            (token_hash,),
        )
        rows = cursor.fetchall()
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Internal server error.") from e

    services: List[ServiceInfo] = []
    for row in rows:
        try:
            last_dt = datetime.fromisoformat(row["last_notification"])
        except ValueError:
            # Fallback parsing
            last_dt = datetime.strptime(row["last_notification"], "%Y-%m-%dT%H:%M:%S.%f%z")
        services.append(
            ServiceInfo(
                serviceId=row["service_id"],
                lastNotification=last_dt,
            )
        )
    return services


# ---------- Run the server ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)