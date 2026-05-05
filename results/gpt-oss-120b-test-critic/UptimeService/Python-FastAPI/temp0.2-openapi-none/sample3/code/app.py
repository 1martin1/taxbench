import os
import sqlite3
import threading
import logging
from datetime import datetime
from typing import List

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from passlib.context import CryptContext

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Security ----------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ---------- Database Setup ----------
DB_PATH = "db.sqlite3"
DB_LOCK = threading.Lock()


def get_db():
    """Provide a thread‑safe SQLite connection."""
    conn = sqlite3.connect(
        DB_PATH,
        detect_types=sqlite3.PARSE_DECLTYPES,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Create the services table if it does not exist."""
    with DB_LOCK, sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_id TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                last_notification TEXT NOT NULL,
                UNIQUE(service_id)
            )
            """
        )
        conn.commit()


# ---------- Pydantic Models ----------
class HeartbeatRequest(BaseModel):
    serviceId: str = Field(..., description="The unique identifier of the service.")
    token: str = Field(..., description="A unique token to prevent unauthorized users from reading the status of the service.")


class ServicesRequest(BaseModel):
    token: str = Field(..., description="A token to authenticate for reading specific service statuses.")


class ServiceInfo(BaseModel):
    serviceId: str = Field(..., description="The unique identifier of the service.")
    lastNotification: datetime = Field(..., description="The last time a heartbeat was received for the service.")


# ---------- FastAPI App ----------
app = FastAPI(
    title="Service Monitoring API",
    version="1.0.0",
    description="API for monitoring the status of services.",
)


@app.on_event("startup")
def on_startup():
    init_db()


# ---------- Custom Exception Handlers ----------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Conform to OpenAPI spec that expects 400 for invalid payloads
    return JSONResponse(
        status_code=400,
        content={"detail": "Invalid request payload."},
    )


# ---------- Endpoints ----------
@app.post(
    "/heartbeat",
    status_code=status.HTTP_200_OK,
    summary="Register a heartbeat for a service",
    responses={
        200: {"description": "Heartbeat registered successfully."},
        400: {"description": "Invalid request payload."},
        403: {"description": "Invalid token."},
        500: {"description": "Internal server error."},
    },
)
def register_heartbeat(
    payload: HeartbeatRequest,
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Accepts a request to signify that a specific service is still alive.
    """
    now_iso = datetime.utcnow().isoformat()
    try:
        with DB_LOCK:
            cur = db.execute(
                "SELECT token_hash FROM services WHERE service_id = ?",
                (payload.serviceId,),
            )
            row = cur.fetchone()
            if row:
                # Verify token
                if not pwd_context.verify(payload.token, row["token_hash"]):
                    raise HTTPException(status_code=403, detail="Invalid token.")
                # Update timestamp
                db.execute(
                    "UPDATE services SET last_notification = ? WHERE service_id = ?",
                    (now_iso, payload.serviceId),
                )
            else:
                # Insert new service with hashed token
                token_hash = pwd_context.hash(payload.token)
                db.execute(
                    """
                    INSERT INTO services (service_id, token_hash, last_notification)
                    VALUES (?, ?, ?)
                    """,
                    (payload.serviceId, token_hash, now_iso),
                )
            db.commit()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error in register_heartbeat")
        raise HTTPException(status_code=500, detail="Internal server error.") from e
    return {"detail": "Heartbeat registered successfully."}


@app.post(
    "/services",
    response_model=List[ServiceInfo],
    status_code=status.HTTP_200_OK,
    summary="Get overview of registered services",
    responses={
        200: {"description": "A list of registered services with their last notification date."},
        500: {"description": "Internal server error."},
    },
)
def list_services(
    payload: ServicesRequest,
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Provides an overview of registered services and their last notification date,
    where the token of this request matches the token sent with the service request.
    """
    try:
        with DB_LOCK:
            cur = db.execute(
                "SELECT service_id, token_hash, last_notification FROM services"
            )
            rows = cur.fetchall()
        result = []
        for row in rows:
            if pwd_context.verify(payload.token, row["token_hash"]):
                result.append(
                    ServiceInfo(
                        serviceId=row["service_id"],
                        lastNotification=row["last_notification"],
                    )
                )
        return result
    except Exception as e:
        logger.exception("Unexpected error in list_services")
        raise HTTPException(status_code=500, detail="Internal server error.") from e


# ---------- Run Server ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000, reload=False)