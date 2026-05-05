import os
import hmac
import hashlib
import sqlite3
from datetime import datetime, timezone
from typing import List

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError("APP_SECRET environment variable must be set")

DB_PATH = "db.sqlite3"
MAX_BODY_SIZE = 1024  # 1 KiB
MAX_SERVICE_ID_LENGTH = 64
MAX_TOKEN_LENGTH = 64

# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def get_token_hash(token: str) -> str:
    """Return a deterministic HMAC‑SHA256 hash of the token using the app secret."""
    return hmac.new(APP_SECRET.encode(), token.encode(), hashlib.sha256).hexdigest()


def get_db_connection():
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS services (
                service_id TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                last_notification TIMESTAMP NOT NULL,
                PRIMARY KEY (service_id, token_hash)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Service Monitoring API",
    version="1.0.0",
    description="API for monitoring the status of services.",
)


@app.on_event("startup")
def on_startup():
    init_db()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class HeartbeatRequest(BaseModel):
    serviceId: str = Field(
        ...,
        description="The unique identifier of the service.",
        max_length=MAX_SERVICE_ID_LENGTH,
    )
    token: str = Field(
        ...,
        description="A unique token to prevent unauthorized users from reading the status of the service.",
        max_length=MAX_TOKEN_LENGTH,
    )

    @validator("serviceId", "token")
    def not_empty(cls, v: str):
        if not v.strip():
            raise ValueError("must not be empty or whitespace")
        return v


class ServicesRequest(BaseModel):
    token: str = Field(
        ...,
        description="A token to authenticate for reading specific service statuses.",
        max_length=MAX_TOKEN_LENGTH,
    )

    @validator("token")
    def not_empty(cls, v: str):
        if not v.strip():
            raise ValueError("must not be empty or whitespace")
        return v


class ServiceInfo(BaseModel):
    serviceId: str = Field(..., description="The unique identifier of the service.")
    lastNotification: datetime = Field(
        ..., description="The last time a heartbeat was received for the service."
    )


# ---------------------------------------------------------------------------
# Middleware to limit request body size
# ---------------------------------------------------------------------------


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    # Read the body up to the limit
    body = await request.body()
    if len(body) > MAX_BODY_SIZE:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "Request body too large"},
        )
    # Re‑inject the body for downstream handlers
    request._receive = lambda: {"type": "http.request", "body": body, "more_body": False}
    return await call_next(request)


# ---------------------------------------------------------------------------
# Custom validation error handler to return 400 instead of 422
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
    status_code=status.HTTP_200_OK,
    summary="Register a heartbeat for a service",
    description="Accepts a request to signify that a specific service is still alive.",
)
def register_heartbeat(payload: HeartbeatRequest):
    now = datetime.now(timezone.utc)
    token_hash = get_token_hash(payload.token)

    conn = get_db_connection()
    try:
        try:
            conn.execute(
                """
                INSERT INTO services (service_id, token_hash, last_notification)
                VALUES (?, ?, ?)
                """,
                (payload.serviceId, token_hash, now),
            )
        except sqlite3.IntegrityError:
            conn.execute(
                """
                UPDATE services
                SET last_notification = ?
                WHERE service_id = ? AND token_hash = ?
                """,
                (now, payload.serviceId, token_hash),
            )
        conn.commit()
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        conn.close()

    return {"detail": "Heartbeat registered successfully."}


@app.post(
    "/services",
    response_model=List[ServiceInfo],
    status_code=status.HTTP_200_OK,
    summary="Get overview of registered services",
    description=(
        "Provides an overview of registered services and their last notification date, "
        "where the token of this request matches the token sent with the service request."
    ),
)
def get_services(payload: ServicesRequest):
    token_hash = get_token_hash(payload.token)

    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT service_id, last_notification
            FROM services
            WHERE token_hash = ?
            """,
            (token_hash,),
        ).fetchall()
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        conn.close()

    result = [
        ServiceInfo(
            serviceId=row["service_id"],
            lastNotification=row["last_notification"]
            if isinstance(row["last_notification"], datetime)
            else datetime.fromisoformat(row["last_notification"]).replace(tzinfo=timezone.utc),
        )
        for row in rows
    ]
    return result


# ---------------------------------------------------------------------------
# Run the application
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000, reload=False)