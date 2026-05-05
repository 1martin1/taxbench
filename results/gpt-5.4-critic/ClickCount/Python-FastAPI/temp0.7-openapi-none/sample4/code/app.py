import os
import sqlite3
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from datetime import date, datetime, time as dt_time, timezone
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict


DB_PATH = "db.sqlite3"
SQLITE_TIMEOUT_SECONDS = 5.0
MAX_CLICKS_RESPONSE = 1000
POST_RATE_LIMIT = 60
GET_RATE_LIMIT = 120
RATE_WINDOW_SECONDS = 60


_rate_limit_lock = threading.Lock()
_rate_limit_store: dict[str, deque[float]] = {}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_db_timestamp(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid timestamp stored in database: {value}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_date_param(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid date") from exc


def validate_direction(value: str) -> str:
    allowed = {"<", ">", "<=", ">="}
    if value not in allowed:
        raise HTTPException(status_code=400, detail="Invalid direction")
    return value


def date_to_utc_bounds(target_date: date) -> tuple[str, str]:
    start_dt = datetime.combine(target_date, dt_time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(target_date, dt_time.max, tzinfo=timezone.utc)
    return start_dt.isoformat(), end_dt.isoformat()


def get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        first = forwarded_for.split(",")[0].strip()
        if first:
            return first
    client = request.client
    if client and client.host:
        return client.host
    return "unknown"


def enforce_rate_limit(request: Request, bucket: str, limit: int) -> None:
    now = time.monotonic()
    client_ip = get_client_ip(request)
    key = f"{bucket}:{client_ip}"

    with _rate_limit_lock:
        timestamps = _rate_limit_store.get(key)
        if timestamps is None:
            timestamps = deque()
            _rate_limit_store[key] = timestamps

        cutoff = now - RATE_WINDOW_SECONDS
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()

        if len(timestamps) >= limit:
            raise HTTPException(status_code=400, detail="Too many requests")

        timestamps.append(now)

        if len(_rate_limit_store) > 10000:
            expired_keys = [
                store_key
                for store_key, store_timestamps in _rate_limit_store.items()
                if not store_timestamps or store_timestamps[-1] < cutoff
            ]
            for store_key in expired_keys:
                _rate_limit_store.pop(store_key, None)


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=SQLITE_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
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
        conn.commit()


class Click(BaseModel):
    id: str
    timestamp: datetime

    model_config = ConfigDict(from_attributes=True)


app = FastAPI(
    title="Click Tracking API",
    version="1.0.0",
    description="API for registering and retrieving user clicks.",
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.post(
    "/click",
    status_code=201,
    responses={
        201: {"description": "Click registered successfully"},
        400: {"description": "Invalid request"},
    },
)
def register_click(request: Request) -> Response:
    enforce_rate_limit(request, "post_click", POST_RATE_LIMIT)

    click_id = str(uuid.uuid4())
    timestamp = utc_now_iso()

    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO clicks (id, timestamp) VALUES (?, ?)",
                (click_id, timestamp),
            )
            conn.commit()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=400, detail="Invalid request") from exc

    return Response(status_code=201)


@app.get(
    "/clicks",
    response_model=List[Click],
    responses={
        200: {"description": "A list of clicks"},
        400: {"description": "Invalid request"},
        404: {"description": "No clicks found"},
    },
)
def retrieve_clicks(
    request: Request,
    date_value: str = Query(..., alias="date", description="The date to filter clicks."),
    direction: str = Query(
        ...,
        description="Specify '<', '<=' or '>' etc. to filter clicks before, before or at or after the date.",
    ),
) -> List[Click]:
    enforce_rate_limit(request, "get_clicks", GET_RATE_LIMIT)

    parsed_date = parse_date_param(date_value)
    validated_direction = validate_direction(direction)
    start_iso, end_iso = date_to_utc_bounds(parsed_date)

    if validated_direction == "<":
        sql = "SELECT id, timestamp FROM clicks WHERE timestamp < ? ORDER BY timestamp ASC LIMIT ?"
        params = (start_iso, MAX_CLICKS_RESPONSE + 1)
    elif validated_direction == "<=":
        sql = "SELECT id, timestamp FROM clicks WHERE timestamp <= ? ORDER BY timestamp ASC LIMIT ?"
        params = (end_iso, MAX_CLICKS_RESPONSE + 1)
    elif validated_direction == ">":
        sql = "SELECT id, timestamp FROM clicks WHERE timestamp > ? ORDER BY timestamp ASC LIMIT ?"
        params = (end_iso, MAX_CLICKS_RESPONSE + 1)
    else:
        sql = "SELECT id, timestamp FROM clicks WHERE timestamp >= ? ORDER BY timestamp ASC LIMIT ?"
        params = (start_iso, MAX_CLICKS_RESPONSE + 1)

    try:
        with get_db() as conn:
            rows = conn.execute(sql, params).fetchall()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=400, detail="Invalid request") from exc

    if not rows:
        raise HTTPException(status_code=404, detail="No clicks found")

    rows = rows[:MAX_CLICKS_RESPONSE]

    result: List[Click] = []
    for row in rows:
        result.append(
            Click(
                id=row["id"],
                timestamp=parse_db_timestamp(row["timestamp"]),
            )
        )

    return result


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)