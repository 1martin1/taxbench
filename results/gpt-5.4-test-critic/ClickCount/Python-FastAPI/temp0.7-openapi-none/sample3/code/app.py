import os
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timezone
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict


DB_PATH = "db.sqlite3"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clicks (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_db_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def date_to_utc_bounds(target_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(target_date, time.min, tzinfo=timezone.utc)
    end = datetime.combine(target_date, time.max, tzinfo=timezone.utc)
    return start, end


class Click(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "timestamp": "2022-01-01T12:00:00+00:00",
            }
        }
    )

    id: str
    timestamp: datetime


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Click Tracking API",
    version="1.0.0",
    description="API for registering and retrieving user clicks.",
    lifespan=lifespan,
)


@app.post("/click", status_code=201, summary="Register a click", description="Endpoint to register a user's click in the database.")
def register_click(response: Response):
    click_id = str(uuid.uuid4())
    timestamp = utc_now_iso()

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO clicks (id, timestamp) VALUES (?, ?)",
            (click_id, timestamp),
        )
        conn.commit()
    except sqlite3.Error:
        raise HTTPException(status_code=400, detail="Invalid request")
    finally:
        conn.close()

    response.headers["Location"] = f"/clicks"
    return {"id": click_id, "timestamp": timestamp}


@app.get(
    "/clicks",
    response_model=list[Click],
    summary="Retrieve clicks",
    description="Endpoint to retrieve clicks before or after a certain date.",
)
def retrieve_clicks(
    date: date = Query(..., description="The date to filter clicks.", example="2022-01-01"),
    direction: Literal["<", ">", "<=", ">="] = Query(
        ...,
        description="Specify '<', '<=' or '>' etc. to filter clicks before, before or at or after the date.",
    ),
):
    start_dt, end_dt = date_to_utc_bounds(date)

    conn = get_connection()
    try:
        rows = conn.execute("SELECT id, timestamp FROM clicks").fetchall()
    finally:
        conn.close()

    filtered = []
    for row in rows:
        ts = parse_db_timestamp(row["timestamp"])

        include = False
        if direction == "<":
            include = ts < start_dt
        elif direction == "<=":
            include = ts <= end_dt
        elif direction == ">":
            include = ts > end_dt
        elif direction == ">=":
            include = ts >= start_dt

        if include:
            filtered.append(
                Click(
                    id=row["id"],
                    timestamp=ts,
                )
            )

    if not filtered:
        raise HTTPException(status_code=404, detail="No clicks found")

    return filtered


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)