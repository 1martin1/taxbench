import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import List

from fastapi import FastAPI, HTTPException, Request, Depends, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, SecretStr, constr, validator
from passlib.context import CryptContext

# ---------- Configuration ----------
DB_PATH = "db.sqlite3"
MAX_SERVICE_ID_LENGTH = 100
MAX_TOKEN_LENGTH = 100
MAX_SERVICES_RETURN = 1000  # safeguard against huge responses
RATE_LIMIT = 60  # max requests
RATE_WINDOW = 60  # per seconds

# ---------- Cryptography ----------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ---------- Database ----------
db_lock = threading.Lock()
rate_lock = threading.Lock()
request_log = {}  # type: dict[tuple[str, str], list[float]]

def get_db_connection() -> sqlite3.Connection:
    """Create a singleton SQLite connection with sensible pragmas."""
    if not hasattr(get_db_connection, "conn"):
        conn = sqlite3.connect(
            DB_PATH,
            detect_types=sqlite3.PARSE_DECLTYPES,
            check_same_thread=False,
            timeout=30,
        )
        conn.row_factory = sqlite3.Row
        # Pragmas for concurrency and safety
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=5000;")
        get_db_connection.conn = conn
    return get_db_connection.conn  # type: ignore

def init_db() -> None:
    """Create the services table if it doesn't exist."""
    conn = get_db_connection()
    with db_lock:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_id TEXT NOT NULL UNIQUE,
                token TEXT NOT NULL,
                last_notification TEXT NOT NULL
            )
            """
        )
        conn.commit()

# ---------- Rate Limiting ----------
def rate_limiter(request: Request) -> None:
    client_ip = request.client.host if request.client else "unknown"
    endpoint = request.url.path
    key = (client_ip, endpoint)
    now = time.time()
    with rate_lock:
        timestamps = request_log.get(key, [])
        # Keep only timestamps within the window
        timestamps = [t for t in timestamps if now - t < RATE_WINDOW]
        if len(timestamps) >= RATE_LIMIT:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too Many Requests",
            )
        timestamps.append(now)
        request_log[key] = timestamps

# ---------- Pydantic models ----------
class HeartbeatRequest(BaseModel):
    serviceId: constr(strip_whitespace=True, min_length=1, max_length=MAX_SERVICE_ID_LENGTH) = Field(
        ..., description="The unique identifier of the service."
    )
    token: SecretStr = Field(
        ..., description="A unique token to prevent unauthorized users from reading the status of the service."
    )

    @validator("token")
    def token_length(cls, v: SecretStr) -> SecretStr:
        token_str = v.get_secret_value()
        if not (1 <= len(token_str) <= MAX_TOKEN_LENGTH):
            raise ValueError(f"Token length must be between 1 and {MAX_TOKEN_LENGTH}")
        return v


class ServicesRequest(BaseModel):
    token: SecretStr = Field(
        ..., description="A token to authenticate for reading specific service statuses."
    )

    @validator("token")
    def token_length(cls, v: SecretStr) -> SecretStr:
        token_str = v.get_secret_value()
        if not (1 <= len(token_str) <= MAX_TOKEN_LENGTH):
            raise ValueError(f"Token length must be between 1 and {MAX_TOKEN_LENGTH}")
        return v


class ServiceInfo(BaseModel):
    serviceId: str = Field(..., description="The unique identifier of the service.")
    lastNotification: datetime = Field(..., description="The last time a heartbeat was received for the service.")

# ---------- FastAPI app ----------
app = FastAPI(
    title="Service Monitoring API",
    version="1.0.0",
    description="API for monitoring the status of services.",
)

@app.on_event("startup")
def on_startup():
    init_db()

# ---------- Helper functions ----------
def upsert_heartbeat(service_id: str, token: str, timestamp: str) -> None:
    """Insert a new service or update its last_notification timestamp."""
    conn = get_db_connection()
    with db_lock:
        cur = conn.cursor()
        # Check if the service already exists
        cur.execute("SELECT token FROM services WHERE service_id = ?", (service_id,))
        row = cur.fetchone()
        if row:
            # Keep the existing token hash, just update timestamp
            cur.execute(
                "UPDATE services SET last_notification = ? WHERE service_id = ?",
                (timestamp, service_id),
            )
        else:
            # Store a hashed version of the token
            token_hash = pwd_context.hash(token)
            cur.execute(
                "INSERT INTO services (service_id, token, last_notification) VALUES (?,?,?)",
                (service_id, token_hash, timestamp),
            )
        conn.commit()


def fetch_services_by_token(token: str) -> List[ServiceInfo]:
    """Return a list of services that match the given token (hashed comparison)."""
    conn = get_db_connection()
    matching: List[ServiceInfo] = []
    with db_lock:
        cur = conn.cursor()
        cur.execute(
            "SELECT service_id, token, last_notification FROM services"
        )
        rows = cur.fetchall()
        for row in rows:
            stored_hash = row["token"]
            if pwd_context.verify(token, stored_hash):
                matching.append(
                    ServiceInfo(
                        serviceId=row["service_id"],
                        lastNotification=datetime.fromisoformat(row["last_notification"]),
                    )
                )
                if len(matching) >= MAX_SERVICES_RETURN:
                    break
    return matching

# ---------- Exception Handlers ----------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Conform to the OpenAPI spec that expects a 400 Bad Request for malformed payloads
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "Invalid request payload"},
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )

# ---------- Endpoints ----------
@app.post(
    "/heartbeat",
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "Heartbeat registered successfully."},
        400: {"description": "Invalid request payload."},
        429: {"description": "Too Many Requests."},
        500: {"description": "Internal server error."},
    },
    dependencies=[Depends(rate_limiter)],
)
def register_heartbeat(payload: HeartbeatRequest):
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        upsert_heartbeat(
            service_id=payload.serviceId,
            token=payload.token.get_secret_value(),
            timestamp=now_iso,
        )
        return {"detail": "Heartbeat registered successfully."}
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Internal server error.")
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error.")


@app.post(
    "/services",
    response_model=List[ServiceInfo],
    responses={
        200: {"description": "A list of registered services with their last notification date."},
        429: {"description": "Too Many Requests."},
        500: {"description": "Internal server error."},
    },
    dependencies=[Depends(rate_limiter)],
)
def get_services(payload: ServicesRequest):
    try:
        services = fetch_services_by_token(payload.token.get_secret_value())
        return services
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Internal server error.")
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error.")


# ---------- Run the application ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)