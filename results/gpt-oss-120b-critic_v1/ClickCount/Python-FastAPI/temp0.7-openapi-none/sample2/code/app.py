import os
import uuid
import sqlite3
import threading
import time
from datetime import datetime, date
from typing import List, Dict

from fastapi import FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
DB_PATH = "db.sqlite3"
MAX_ROWS = 10_000               # Upper bound for number of rows returned
RATE_LIMIT = 60                 # Max requests per interval per IP
RATE_INTERVAL = 60              # Interval in seconds

# ----------------------------------------------------------------------
# Rate Limiting Utilities
# ----------------------------------------------------------------------
class SimpleRateLimiter:
    def __init__(self, limit: int, interval: int):
        self.limit = limit
        self.interval = interval
        self._records: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def is_allowed(self, ip: str) -> bool:
        now = time.time()
        with self._lock:
            timestamps = self._records.get(ip, [])
            # Remove timestamps outside the interval
            timestamps = [ts for ts in timestamps if now - ts < self.interval]
            if len(timestamps) >= self.limit:
                # Too many requests
                self._records[ip] = timestamps
                return False
            timestamps.append(now)
            self._records[ip] = timestamps
            return True

rate_limiter = SimpleRateLimiter(RATE_LIMIT, RATE_INTERVAL)

def get_client_ip(request: Request) -> str:
    # X-Forwarded-For may contain multiple IPs, we take the first one
    forward = request.headers.get("x-forwarded-for")
    if forward:
        return forward.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

def enforce_rate_limit(request: Request):
    ip = get_client_ip(request)
    if not rate_limiter.is_allowed(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Try again later."
        )

# ----------------------------------------------------------------------
# FastAPI Application
# ----------------------------------------------------------------------
app = FastAPI(
    title="Click Tracking API",
    version="1.0.0",
    description="API for registering and retrieving user clicks."
)

# ----------------------------------------------------------------------
# Database Helpers
# ----------------------------------------------------------------------
def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    # Verify we can write to the directory before attempting to create the file
    dir_path = os.path.abspath(os.path.dirname(DB_PATH)) or "."
    if not os.access(dir_path, os.W_OK):
        raise RuntimeError(f"Insufficient permissions to write to directory '{dir_path}'.")
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clicks (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()

@app.on_event("startup")
def on_startup():
    try:
        init_db()
    except Exception as exc:
        # Fail fast with a clear message
        raise RuntimeError(f"Failed to initialise database: {exc}")

# ----------------------------------------------------------------------
# Pydantic Models
# ----------------------------------------------------------------------
class Click(BaseModel):
    id: str = Field(..., description="The unique identifier for the click.")
    timestamp: datetime = Field(..., description="The timestamp of the click.")

    class Config:
        orm_mode = True

# ----------------------------------------------------------------------
# Utility Functions
# ----------------------------------------------------------------------
def parse_date(date_str: str) -> date:
    try:
        return date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid date format. Expected YYYY-MM-DD."
        )

def operator_from_direction(direction: str) -> str:
    # Mapping ensures only allowed operators are used
    allowed = {"<", "<=", ">", ">="}
    if direction not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid direction operator."
        )
    return direction

# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------
@app.post(
    "/click",
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Click registered successfully"},
        400: {"description": "Invalid request"},
        429: {"description": "Rate limit exceeded"},
    },
)
def register_click(request: Request):
    enforce_rate_limit(request)

    click_id = str(uuid.uuid4())
    # Store timestamps in UTC ISO format with trailing Z
    timestamp = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO clicks (id, timestamp) VALUES (?, ?);",
            (click_id, timestamp),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not register click."
        )
    finally:
        conn.close()
    return {"id": click_id, "timestamp": timestamp}


@app.get(
    "/clicks",
    response_model=List[Click],
    responses={
        200: {"description": "A list of clicks"},
        400: {"description": "Invalid request"},
        404: {"description": "No clicks found"},
        429: {"description": "Rate limit exceeded"},
    },
)
def get_clicks(
    request: Request,
    date: str = Query(..., description="The date to filter clicks.", example="2022-01-01"),
    direction: str = Query(
        ...,
        description="Specify '<', '<=' or '>' etc. to filter clicks before, before or at or after the date.",
        regex="^(<|<=|>|>=)$",
    ),
):
    enforce_rate_limit(request)

    filter_date = parse_date(date)
    iso_date = filter_date.isoformat()  # start of the day in ISO format

    op = operator_from_direction(direction)

    sql = f"SELECT id, timestamp FROM clicks WHERE timestamp {op} ? ORDER BY timestamp ASC LIMIT ?;"
    conn = get_connection()
    try:
        cur = conn.execute(sql, (iso_date, MAX_ROWS))
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        # According to the OpenAPI spec a 404 response is defined, but returning an empty list is more
        # client‑friendly while still conforming to the schema (an empty array is a valid response).
        return []

    clicks = [
        Click(
            id=row["id"],
            timestamp=datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")),
        )
        for row in rows
    ]
    return clicks

# ----------------------------------------------------------------------
# Run the application
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)