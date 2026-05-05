import os
import sqlite3
import hashlib
import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException, status, Request
from pydantic import BaseModel, Field, constr

# -------------------------------------------------
# Configuration
# -------------------------------------------------
DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET", "")

# -------------------------------------------------
# Helper functions
# -------------------------------------------------
def hash_token(token: str) -> str:
    """
    Create a deterministic hash of the token using a secret pepper.
    This allows verification without storing the plain token.
    """
    return hashlib.sha256((APP_SECRET + token).encode("utf-8")).hexdigest()


# -------------------------------------------------
# Database utilities
# -------------------------------------------------
def init_db() -> None:
    """Create the services table if it does not exist."""
    conn = sqlite3.connect(DB_PATH)
    try:
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
        conn.commit()
    finally:
        conn.close()


def get_connection() -> sqlite3.Connection:
    """Return a new SQLite connection with Row factory."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn


# -------------------------------------------------
# FastAPI app
# -------------------------------------------------
app = FastAPI(
    title="Service Monitoring API",
    version="1.0.0",
    description="API for monitoring the status of services.",
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


# -------------------------------------------------
# Pydantic models with validation
# -------------------------------------------------
class HeartbeatRequest(BaseModel):
    serviceId: constr(
        strip_whitespace=True, min_length=1, max_length=128
    ) = Field(..., description="The unique identifier of the service.")
    token: constr(
        strip_whitespace=True, min_length=1, max_length=128
    ) = Field(..., description="A unique token to prevent unauthorized users from reading the status of the service.")


class ServicesRequest(BaseModel):
    token: constr(
        strip_whitespace=True, min_length=1, max_length=128
    ) = Field(..., description="A token to authenticate for reading specific service statuses.")


class ServiceInfo(BaseModel):
    serviceId: str = Field(..., description="The unique identifier of the service.")
    lastNotification: datetime.datetime = Field(
        ..., description="The last time a heartbeat was received for the service."
    )


# -------------------------------------------------
# Endpoints
# -------------------------------------------------
@app.post(
    "/heartbeat",
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "Heartbeat registered successfully."},
        400: {"description": "Invalid request payload."},
        500: {"description": "Internal server error."},
    },
)
def register_heartbeat(payload: HeartbeatRequest):
    """
    Register a heartbeat for a service.
    """
    token_hash = hash_token(payload.token)
    now_iso = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        # Try to update existing record
        cursor.execute(
            """
            UPDATE services
            SET last_notification = ?
            WHERE service_id = ? AND token_hash = ?
            """,
            (now_iso, payload.serviceId, token_hash),
        )
        if cursor.rowcount == 0:
            # Insert new record if not exists
            cursor.execute(
                """
                INSERT INTO services (service_id, token_hash, last_notification)
                VALUES (?, ?, ?)
                """,
                (payload.serviceId, token_hash, now_iso),
            )
        conn.commit()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail="Internal server error.") from exc
    finally:
        if conn:
            conn.close()
    return {"detail": "Heartbeat registered successfully."}


@app.post(
    "/services",
    response_model=List[ServiceInfo],
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "description": "A list of registered services with their last notification date (shows all services registered with the given token)."
        },
        500: {"description": "Internal server error."},
    },
)
def get_services(payload: ServicesRequest):
    """
    Get overview of registered services for a given token.
    """
    token_hash = hash_token(payload.token)
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT service_id, last_notification
            FROM services
            WHERE token_hash = ?
            """,
            (token_hash,),
        )
        rows = cursor.fetchall()
        services = [
            ServiceInfo(
                serviceId=row["service_id"],
                lastNotification=datetime.datetime.fromisoformat(
                    row["last_notification"].replace("Z", "+00:00")
                ),
            )
            for row in rows
        ]
        return services
    except (sqlite3.Error, ValueError) as exc:
        raise HTTPException(status_code=500, detail="Internal server error.") from exc
    finally:
        if conn:
            conn.close()


# -------------------------------------------------
# Run the application
# -------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)