import os
import sqlite3
import threading
import time as time_module
import uuid
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, List, Literal, Optional

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

DB_PATH = "db.sqlite3"
SQLITE_TIMEOUT_SECONDS = 10.0
MAX_CLICKS_RETURNED = 1000
MAX_CLICKS_PER_MINUTE = 120
WRITE_RATE_LIMIT_WINDOW_SECONDS = 60


app = FastAPI(
    title="Click Tracking API",
    version="1.0.0",
    description="API for registering and retrieving user clicks.",
)


class Click(BaseModel):
    id: str
    timestamp: datetime

    model_config = ConfigDict(from_attributes=True)


class ClickCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_token: Optional[str] = Field(
        default=None,
        description="Optional client token. If provided, it must be a string.",
    )


_rate_limit_lock = threading.Lock()
_rate_limit_store: dict[str, list[float]] = {}


def _get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        first_ip = forwarded_for.split(",")[0].strip()
        if first_ip:
            return first_ip
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _enforce_write_rate_limit(request: Request) -> None:
    client_ip = _get_client_ip(request)
    now = time_module.monotonic()
    window_start = now - WRITE_RATE_LIMIT_WINDOW_SECONDS

    with _rate_limit_lock:
        timestamps = _rate_limit_store.get(client_ip, [])
        timestamps = [ts for ts in timestamps if ts >= window_start]

        if len(timestamps) >= MAX_CLICKS_PER_MINUTE:
            _rate_limit_store[client_ip] = timestamps
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests",
            )

        timestamps.append(now)
        _rate_limit_store[client_ip] = timestamps


@contextmanager
def get_db():
    conn = None
    try:
        conn = sqlite3.connect(
            DB_PATH,
            timeout=SQLITE_TIMEOUT_SECONDS,
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
    except sqlite3.Error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        )
    finally:
        if conn is not None:
            conn.close()


def init_db() -> None:
    try:
        with get_db() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS clicks (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL
                )
                """
            )
    except HTTPException:
        raise
    except Exception:
        raise RuntimeError("Failed to initialize database")


@app.on_event("startup")
def on_startup() -> None:
    try:
        init_db()
    except Exception as exc:
        raise RuntimeError("Application startup failed") from exc


@app.exception_handler(sqlite3.Error)
def sqlite_exception_handler(request: Request, exc: sqlite3.Error) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Database error"},
    )


@app.post(
    "/click",
    status_code=201,
    response_model=Click,
    summary="Register a click",
    description="Endpoint to register a user's click in the database.",
    responses={
        201: {"description": "Click registered successfully"},
        400: {"description": "Invalid request"},
        429: {"description": "Too many requests"},
        500: {"description": "Internal server error"},
    },
)
def register_click(
    request: Request,
    payload: Annotated[Optional[ClickCreateRequest], Body(default=None)],
) -> Click:
    _enforce_write_rate_limit(request)

    click_count = 0
    try:
        with get_db() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM clicks").fetchone()
            click_count = int(row["count"]) if row is not None else 0
    except HTTPException:
        raise

    if click_count >= 100000:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Storage limit reached",
        )

    click_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc)

    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO clicks (id, timestamp) VALUES (?, ?)",
                (click_id, timestamp.isoformat()),
            )
    except HTTPException:
        raise

    return Click(id=click_id, timestamp=timestamp)


@app.get(
    "/clicks",
    response_model=List[Click],
    summary="Retrieve clicks",
    description="Endpoint to retrieve clicks before or after a certain date.",
    responses={
        200: {"description": "A list of clicks"},
        400: {"description": "Invalid request"},
        404: {"description": "No clicks found"},
        500: {"description": "Internal server error"},
    },
)
def retrieve_clicks(
    date: date = Query(
        ...,
        description="The date to filter clicks.",
        example="2022-01-01",
    ),
    direction: Literal["<", ">", "<=", ">="] = Query(
        ...,
        description="Specify '<', '<=' or '>' etc. to filter clicks before, before or at or after the date.",
    ),
) -> List[Click]:
    start_of_day = datetime.combine(date, time.min, tzinfo=timezone.utc)
    start_of_next_day = start_of_day + timedelta(days=1)

    if direction == "<":
        comparison_operator = "<"
        comparison_value = start_of_day
    elif direction == "<=":
        comparison_operator = "<"
        comparison_value = start_of_next_day
    elif direction == ">":
        comparison_operator = ">="
        comparison_value = start_of_next_day
    else:  # direction == ">="
        comparison_operator = ">="
        comparison_value = start_of_day

    query = (
        f"SELECT id, timestamp FROM clicks "
        f"WHERE timestamp {comparison_operator} ? "
        f"ORDER BY timestamp ASC "
        f"LIMIT ?"
    )

    try:
        with get_db() as conn:
            rows = conn.execute(
                query,
                (comparison_value.isoformat(), MAX_CLICKS_RETURNED + 1),
            ).fetchall()
    except HTTPException:
        raise

    if not rows:
        raise HTTPException(status_code=404, detail="No clicks found")

    rows = rows[:MAX_CLICKS_RETURNED]

    result = []
    for row in rows:
        try:
            parsed_timestamp = datetime.fromisoformat(row["timestamp"])
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Database error",
            )
        result.append(Click(id=row["id"], timestamp=parsed_timestamp))

    return result


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)