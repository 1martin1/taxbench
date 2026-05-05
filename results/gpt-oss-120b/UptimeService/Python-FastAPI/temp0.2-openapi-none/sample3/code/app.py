import os
import sqlite3
import threading
from datetime import datetime
from typing import List

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field

# ---------- Database Setup ----------
DB_PATH = "db.sqlite3"
DB_LOCK = threading.Lock()


def get_db():
    """Provide a thread‑safe SQLite connection."""
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False)
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
                token TEXT NOT NULL,
                last_notification TIMESTAMP NOT NULL,
                UNIQUE(service_id, token)
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
app = FastAPI(title="Service Monitoring API", version="1.0.0", description="API for monitoring the status of services.")


@app.on_event("startup")
def on_startup():
    init_db()


# ---------- Endpoints ----------
@app.post("/heartbeat", status_code=status.HTTP_200_OK, summary="Register a heartbeat for a service")
def register_heartbeat(payload: HeartbeatRequest, db: sqlite3.Connection = Depends(get_db)):
    """
    Accepts a request to signify that a specific service is still alive.
    """
    now = datetime.utcnow()
    try:
        with DB_LOCK:
            # Try to update existing record
            cur = db.execute(
                """
                UPDATE services
                SET last_notification = ?
                WHERE service_id = ? AND token = ?
                """,
                (now, payload.serviceId, payload.token),
            )
            if cur.rowcount == 0:
                # Insert new record if not exists
                db.execute(
                    """
                    INSERT INTO services (service_id, token, last_notification)
                    VALUES (?, ?, ?)
                    """,
                    (payload.serviceId, payload.token, now),
                )
            db.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error.") from e
    return {"detail": "Heartbeat registered successfully."}


@app.post(
    "/services",
    response_model=List[ServiceInfo],
    status_code=status.HTTP_200_OK,
    summary="Get overview of registered services",
)
def list_services(payload: ServicesRequest, db: sqlite3.Connection = Depends(get_db)):
    """
    Provides an overview of registered services and their last notification date,
    where the token of this request matches the token sent with the service request.
    """
    try:
        cur = db.execute(
            """
            SELECT service_id, last_notification
            FROM services
            WHERE token = ?
            ORDER BY service_id ASC
            """,
            (payload.token,),
        )
        rows = cur.fetchall()
        result = [
            ServiceInfo(
                serviceId=row["service_id"],
                lastNotification=row["last_notification"],
            )
            for row in rows
        ]
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error.") from e


# ---------- Run Server ----------
if __name__ == "__main__":
    import uvicorn

    # Host and port as required
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)