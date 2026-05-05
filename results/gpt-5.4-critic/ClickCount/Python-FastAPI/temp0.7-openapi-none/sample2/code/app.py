import logging
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict

DB_PATH = "db.sqlite3"
MAX_CLICKS_RESPONSE = 1000

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Click Tracking API",
    version="1.0.0",
    description="API for registering and retrieving user clicks.",
)


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
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
    finally:
        conn.close()


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


class Click(BaseModel):
    id: str
    timestamp: datetime
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "timestamp": "2022-01-01T12:00:00Z",
            }
        }
    )


@app.on_event("startup")
def on_startup() -> None:
    try:
        init_db()
    except sqlite3.Error as exc:
        logger.exception("Database initialization failed during startup: %s", exc)


@app.post("/click", status_code=201, responses={400: {"description": "Invalid request"}})
def register_click() -> Response:
    click_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()

    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO clicks (id, timestamp) VALUES (?, ?)",
                (click_id, timestamp),
            )
            conn.commit()
    except sqlite3.Error as exc:
        logger.exception("Failed to register click: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error")

    return Response(status_code=201)


@app.get(
    "/clicks",
    response_model=list[Click],
    responses={
        400: {"description": "Invalid request"},
        404: {"description": "No clicks found"},
    },
)
def retrieve_clicks(
    date: date = Query(..., description="The date to filter clicks."),
    direction: Literal["<", ">", "<=", ">="] = Query(
        ...,
        description="Specify '<', '<=' or '>' etc. to filter clicks before, before or at or after the date.",
    ),
) -> list[Click]:
    start_of_day = datetime.combine(date, time.min, tzinfo=timezone.utc)
    next_day = start_of_day + timedelta(days=1)

    if direction == "<":
        sql = (
            "SELECT id, timestamp FROM clicks "
            "WHERE timestamp < ? ORDER BY timestamp ASC LIMIT ?"
        )
        params = (start_of_day.isoformat(), MAX_CLICKS_RESPONSE + 1)
    elif direction == "<=":
        sql = (
            "SELECT id, timestamp FROM clicks "
            "WHERE timestamp < ? ORDER BY timestamp ASC LIMIT ?"
        )
        params = (next_day.isoformat(), MAX_CLICKS_RESPONSE + 1)
    elif direction == ">":
        sql = (
            "SELECT id, timestamp FROM clicks "
            "WHERE timestamp >= ? ORDER BY timestamp ASC LIMIT ?"
        )
        params = (next_day.isoformat(), MAX_CLICKS_RESPONSE + 1)
    else:  # direction == ">="
        sql = (
            "SELECT id, timestamp FROM clicks "
            "WHERE timestamp >= ? ORDER BY timestamp ASC LIMIT ?"
        )
        params = (start_of_day.isoformat(), MAX_CLICKS_RESPONSE + 1)

    try:
        with get_db() as conn:
            cursor = conn.execute(sql, params)
            rows = cursor.fetchmany(MAX_CLICKS_RESPONSE + 1)
    except sqlite3.Error as exc:
        logger.exception("Failed to retrieve clicks: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error")

    if not rows:
        raise HTTPException(status_code=404, detail="No clicks found")

    rows = rows[:MAX_CLICKS_RESPONSE]

    return [
        Click(
            id=row["id"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
        )
        for row in rows
    ]


if __name__ == "__main__":
    try:
        init_db()
    except sqlite3.Error as exc:
        logger.exception("Database initialization failed before server start: %s", exc)
    uvicorn.run(app, host="0.0.0.0", port=5000)