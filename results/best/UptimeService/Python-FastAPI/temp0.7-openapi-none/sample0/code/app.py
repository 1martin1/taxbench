import sqlite3
from datetime import datetime, timezone
from typing import List

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from passlib.hash import pbkdf2_sha256

DB_PATH = "db.sqlite3"

app = FastAPI(
    title="Service Monitoring API",
    version="1.0.0",
    description="API for monitoring the status of services.",
    openapi_version="3.0.3",
)


# ---------- Database Utilities ----------
def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS services (
                service_id TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                last_notification TEXT NOT NULL,
                PRIMARY KEY (service_id, token_hash)
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_services_token_hash
            ON services (token_hash)
            """
        )
        conn.commit()
    finally:
        conn.close()


@app.on_event("startup")
def on_startup() -> None:
    init_db()


# ---------- Models ----------
class HeartbeatRequest(BaseModel):
    serviceId: str = Field(..., min_length=1)
    token: str = Field(..., min_length=1)


class TokenRequest(BaseModel):
    token: str = Field(..., min_length=1)


class ServiceInfo(BaseModel):
    serviceId: str
    lastNotification: str


# ---------- Exception Handlers ----------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={"detail": "Invalid request payload."},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


# ---------- Routes ----------
@app.post("/heartbeat", responses={200: {"description": "Heartbeat registered successfully."}, 400: {"description": "Invalid request payload."}, 500: {"description": "Internal server error."}})
async def register_heartbeat(payload: HeartbeatRequest):
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        token_hash = pbkdf2_sha256.hash(payload.token)
        cur.execute(
            """
            INSERT INTO services (service_id, token_hash, last_notification)
            VALUES (?, ?, ?)
            ON CONFLICT(service_id, token_hash)
            DO UPDATE SET last_notification=excluded.last_notification
            """,
            (payload.serviceId, token_hash, now_iso),
        )
        conn.commit()
        return {"detail": "Heartbeat registered successfully."}
    finally:
        conn.close()


@app.post("/services", response_model=List[ServiceInfo], responses={200: {"description": "A list of registered services with their last notification date (shows all services registered with the given token)."}, 500: {"description": "Internal server error."}})
async def list_services(payload: TokenRequest):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        # Получаем все записи и проверяем токен через verify
        cur.execute("SELECT service_id, token_hash, last_notification FROM services ORDER BY service_id")
        rows = cur.fetchall()
        result = []
        for row in rows:
            if pbkdf2_sha256.verify(payload.token, row["token_hash"]):
                result.append(ServiceInfo(serviceId=row["service_id"], lastNotification=row["last_notification"]))
        return result
    finally:
        conn.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)