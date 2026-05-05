import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "db.sqlite3"
MAX_CLICKS = 10000
MAX_RETURNED_CLICKS = 1000


app = FastAPI(
    title="Click Tracking API",
    version="1.0.0",
    description="API for registering and retrieving user clicks.",
)


class Click(BaseModel):
    id: str
    timestamp: datetime = Field(
        ...,
        json_schema_extra={"example": "2022-01-01T12:00:00Z"},
    )

    class Config:
        json_schema_extra = {
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "timestamp": "2022-01-01T12:00:00Z",
            }
        }


@contextmanager
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def to_storage_timestamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_storage_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def init_db() -> None:
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
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
    except sqlite3.Error as exc:
        raise RuntimeError("Database initialization failed") from exc


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.post(
    "/click",
    status_code=201,
    summary="Register a click",
    description="Endpoint to register a user's click in the database.",
)
def register_click():
    click_id = str(uuid.uuid4())
    timestamp = to_storage_timestamp(datetime.now(timezone.utc))

    try:
        with get_db() as conn:
            current_count = conn.execute("SELECT COUNT(*) AS count FROM clicks").fetchone()["count"]
            if current_count >= MAX_CLICKS:
                raise HTTPException(status_code=400, detail="Invalid request")
            conn.execute(
                "INSERT INTO clicks (id, timestamp) VALUES (?, ?)",
                (click_id, timestamp),
            )
    except HTTPException:
        raise
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Internal server error")

    return Response(status_code=201)


@app.get(
    "/clicks",
    response_model=list[Click],
    summary="Retrieve clicks",
    description="Endpoint to retrieve clicks before or after a certain date.",
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
):
    start_of_day = datetime.combine(date, time.min, tzinfo=timezone.utc)
    next_day = start_of_day + timedelta(days=1)

    if direction == "<":
        query = """
            SELECT id, timestamp
            FROM clicks
            WHERE timestamp < ?
            ORDER BY timestamp ASC
            LIMIT ?
        """
        params = (to_storage_timestamp(start_of_day), MAX_RETURNED_CLICKS + 1)
    elif direction == "<=":
        query = """
            SELECT id, timestamp
            FROM clicks
            WHERE timestamp < ?
            ORDER BY timestamp ASC
            LIMIT ?
        """
        params = (to_storage_timestamp(next_day), MAX_RETURNED_CLICKS + 1)
    elif direction == ">":
        query = """
            SELECT id, timestamp
            FROM clicks
            WHERE timestamp >= ?
            AND timestamp < ?
            ORDER BY timestamp ASC
            LIMIT ?
        """
        params = (
            to_storage_timestamp(next_day),
            "9999-12-31T23:59:59Z",
            MAX_RETURNED_CLICKS + 1,
        )
    else:  # >=
        query = """
            SELECT id, timestamp
            FROM clicks
            WHERE timestamp >= ?
            ORDER BY timestamp ASC
            LIMIT ?
        """
        params = (to_storage_timestamp(start_of_day), MAX_RETURNED_CLICKS + 1)

    try:
        with get_db() as conn:
            rows = conn.execute(query, params).fetchall()
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Internal server error")

    if not rows:
        raise HTTPException(status_code=404, detail="No clicks found")

    if len(rows) > MAX_RETURNED_CLICKS:
        raise HTTPException(status_code=400, detail="Invalid request")

    return [
        Click(
            id=row["id"],
            timestamp=from_storage_timestamp(row["timestamp"]),
        )
        for row in rows
    ]


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)