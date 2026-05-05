import os
import sqlite3
import time as time_module
import uuid
from collections import deque
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Deque, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = str(BASE_DIR / "db.sqlite3")

MAX_CLICKS_TOTAL = 100000
MAX_CLICKS_QUERY_RESULTS = 1000
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 120


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    os.makedirs(BASE_DIR, exist_ok=True)
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clicks (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_clicks_timestamp ON clicks(timestamp)"
        )


class Click(BaseModel):
    id: str = Field(..., description="The unique identifier for the click.")
    timestamp: datetime = Field(..., description="The timestamp of the click.")


class RateLimiter:
    def __init__(self, window_seconds: int, max_requests: int) -> None:
        self.window_seconds = window_seconds
        self.max_requests = max_requests
        self._requests: Dict[str, Deque[float]] = {}
        self._lock = Lock()

    def is_allowed(self, key: str) -> bool:
        now = time_module.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            bucket = self._requests.get(key)
            if bucket is None:
                bucket = deque()
                self._requests[key] = bucket

            while bucket and bucket[0] < cutoff:
                bucket.popleft()

            if len(bucket) >= self.max_requests:
                return False

            bucket.append(now)

            if len(self._requests) > 10000:
                stale_keys = [k for k, v in self._requests.items() if not v or v[-1] < cutoff]
                for stale_key in stale_keys:
                    self._requests.pop(stale_key, None)

            return True


rate_limiter = RateLimiter(
    window_seconds=RATE_LIMIT_WINDOW_SECONDS,
    max_requests=RATE_LIMIT_MAX_REQUESTS,
)

app = FastAPI(
    title="Click Tracking API",
    version="1.0.0",
    description="API for registering and retrieving user clicks.",
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": "Invalid request"})


@app.middleware("http")
async def throttle_requests(request: Request, call_next):
    client_host = request.client.host if request.client else "unknown"
    if not rate_limiter.is_allowed(client_host):
        return JSONResponse(status_code=429, content={"detail": "Too many requests"})
    return await call_next(request)


@app.on_event("startup")
def startup() -> None:
    init_db()


def parse_db_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def get_total_click_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS count FROM clicks").fetchone()
    return int(row["count"])


def compute_boundary(date_value: date, direction: str) -> str:
    start_of_day = datetime.combine(date_value, time.min, tzinfo=timezone.utc)
    next_day = start_of_day + timedelta(days=1)

    if direction in ("<", ">="):
        return to_iso_z(start_of_day)
    if direction in ("<=", ">"):
        return to_iso_z(next_day)
    raise HTTPException(status_code=400, detail="Invalid request")


@app.post(
    "/click",
    status_code=201,
    summary="Register a click",
    responses={400: {"description": "Invalid request"}},
)
def register_click(request: Request) -> Response:
    content_length = request.headers.get("content-length")
    if content_length not in (None, "", "0"):
        raise HTTPException(status_code=400, detail="Invalid request")

    click_id = str(uuid.uuid4())
    timestamp_str = to_iso_z(utc_now())

    with get_db() as conn:
        total_clicks = get_total_click_count(conn)
        if total_clicks >= MAX_CLICKS_TOTAL:
            raise HTTPException(status_code=400, detail="Invalid request")

        conn.execute(
            "INSERT INTO clicks (id, timestamp) VALUES (?, ?)",
            (click_id, timestamp_str),
        )

    return Response(status_code=201)


@app.get(
    "/clicks",
    response_model=List[Click],
    summary="Retrieve clicks",
    responses={
        400: {"description": "Invalid request"},
        404: {"description": "No clicks found"},
    },
)
def retrieve_clicks(
    date_value: date = Query(
        ...,
        alias="date",
        description="The date to filter clicks.",
        examples=["2022-01-01"],
    ),
    direction: str = Query(
        ...,
        description="Specify '<', '<=' or '>' etc. to filter clicks before, before or at or after the date.",
    ),
) -> List[Click]:
    if direction not in {"<", ">", "<=", ">="}:
        raise HTTPException(status_code=400, detail="Invalid request")

    boundary = compute_boundary(date_value, direction)

    query = f"""
        SELECT id, timestamp
        FROM clicks
        WHERE timestamp {direction} ?
        ORDER BY timestamp ASC
        LIMIT ?
    """

    with get_db() as conn:
        rows = conn.execute(query, (boundary, MAX_CLICKS_QUERY_RESULTS + 1)).fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail="No clicks found")

    if len(rows) > MAX_CLICKS_QUERY_RESULTS:
        raise HTTPException(status_code=400, detail="Invalid request")

    return [
        Click(id=row["id"], timestamp=parse_db_timestamp(row["timestamp"]))
        for row in rows
    ]


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)