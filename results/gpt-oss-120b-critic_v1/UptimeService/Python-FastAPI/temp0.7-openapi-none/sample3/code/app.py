import os
import hmac
import hashlib
import sqlite3
from datetime import datetime
from typing import List, Optional

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, Field, validator

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
DATABASE_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET", "default-secret")  # Should be set in production
MAX_BODY_SIZE = 10 * 1024  # 10 KiB limit for request bodies
MAX_SERVICE_ID_LEN = 255
MAX_TOKEN_LEN = 255
MAX_PAGE_LIMIT = 100  # Upper bound for pagination limit


# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #
def hash_token(token: str) -> str:
    """
    Derive a deterministic HMAC‑SHA256 hash of the token using the application secret.
    The result is stored in the database instead of the plain token.
    """
    return hmac.new(APP_SECRET.encode(), token.encode(), hashlib.sha256).hexdigest()


# --------------------------------------------------------------------------- #
# Database utilities
# --------------------------------------------------------------------------- #
def get_db():
    """Dependency that provides a SQLite connection."""
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Create the services table. Dropping the old table ensures the schema is up‑to‑date."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS services")
    cursor.execute(
        """
        CREATE TABLE services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service_id TEXT NOT NULL,
            token_hash TEXT NOT NULL,
            last_notification TEXT NOT NULL,
            UNIQUE(service_id, token_hash)
        )
        """
    )
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------- #
# FastAPI application
# --------------------------------------------------------------------------- #
app = FastAPI(
    title="Service Monitoring API",
    version="1.0.0",
    description="API for monitoring the status of services.",
)


# --------------------------------------------------------------------------- #
# Middleware to enforce request body size limits
# --------------------------------------------------------------------------- #
class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_body_size: int):
        super().__init__(app)
        self.max_body_size = max_body_size

    async def dispatch(self, request: Request, call_next):
        # Only limit JSON bodies for POST methods (the two endpoints we expose)
        if request.method == "POST":
            body = await request.body()
            if len(body) > self.max_body_size:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Request body too large",
                )
            # Re‑inject the body so downstream handlers can read it
            async def receive():
                return {"type": "http.request", "body": body}
            request._receive = receive  # type: ignore
        response = await call_next(request)
        return response


app.add_middleware(BodySizeLimitMiddleware, max_body_size=MAX_BODY_SIZE)


# --------------------------------------------------------------------------- #
# Pydantic models with validation
# --------------------------------------------------------------------------- #
class HeartbeatRequest(BaseModel):
    serviceId: str = Field(..., description="The unique identifier of the service.", max_length=MAX_SERVICE_ID_LEN)
    token: str = Field(..., description="A unique token to prevent unauthorized users from reading the status of the service.", max_length=MAX_TOKEN_LEN)

    @validator("serviceId", "token")
    def not_empty(cls, v: str):
        if not v.strip():
            raise ValueError("must not be empty or whitespace")
        return v


class ServicesRequest(BaseModel):
    token: str = Field(..., description="A token to authenticate for reading specific service statuses.", max_length=MAX_TOKEN_LEN)

    @validator("token")
    def not_empty(cls, v: str):
        if not v.strip():
            raise ValueError("must not be empty or whitespace")
        return v


class ServiceInfo(BaseModel):
    serviceId: str = Field(..., description="The unique identifier of the service.")
    lastNotification: datetime = Field(..., description="The last time a heartbeat was received for the service.")


# --------------------------------------------------------------------------- #
# Application startup
# --------------------------------------------------------------------------- #
@app.on_event("startup")
def on_startup():
    init_db()


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.post(
    "/heartbeat",
    status_code=status.HTTP_200_OK,
    summary="Register a heartbeat for a service",
)
def register_heartbeat(
    payload: HeartbeatRequest,
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Accepts a request to signify that a specific service is still alive.
    """
    now_iso = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    token_hash = hash_token(payload.token)

    try:
        cursor = db.cursor()
        cursor.execute(
            """
            INSERT INTO services (service_id, token_hash, last_notification)
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
    response_model=List[ServiceInfo],
    status_code=status.HTTP_200_OK,
    summary="Get overview of registered services",
)
def get_services(
    payload: ServicesRequest,
    limit: int = 100,
    offset: int = 0,
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Provides an overview of registered services and their last notification date,
    where the token of this request matches the token sent with the service request.
    Supports pagination via `limit` and `offset` query parameters.
    """
    # Enforce pagination limits
    if limit < 1:
        raise HTTPException(status_code=400, detail="`limit` must be at least 1")
    if limit > MAX_PAGE_LIMIT:
        limit = MAX_PAGE_LIMIT
    if offset < 0:
        raise HTTPException(status_code=400, detail="`offset` cannot be negative")

    token_hash = hash_token(payload.token)

    try:
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT service_id, last_notification
            FROM services
            WHERE token_hash = ?
            ORDER BY service_id ASC
            LIMIT ? OFFSET ?
            """,
            (token_hash, limit, offset),
        )
        rows = cursor.fetchall()
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Internal server error.") from e

    services = [
        ServiceInfo(
            serviceId=row["service_id"],
            lastNotification=datetime.fromisoformat(row["last_notification"].replace("Z", "+00:00")),
        )
        for row in rows
    ]

    return services


# --------------------------------------------------------------------------- #
# Run server
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Ensure the secret is loaded (no further use in this script)
    _ = APP_SECRET
    uvicorn.run(app, host="0.0.0.0", port=5000)