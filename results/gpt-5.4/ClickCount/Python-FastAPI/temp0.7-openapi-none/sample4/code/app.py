import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, time, timezone
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict


DB_PATH = "db.sqlite3"


app = FastAPI(
    title="Click Tracking API",
    version="1.0.0",
    description="API for registering and retrieving user clicks.",
)


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


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
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
            "CREATE INDEX IF NOT EXISTS idx_clicks_timestamp ON clicks (timestamp)"
        )


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
    timestamp = datetime.now(timezone.utc).isoformat()

    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO clicks (id, timestamp) VALUES (?, ?)",
                (click_id, timestamp),
            )
    except sqlite3.Error:
        raise HTTPException(status_code=400, detail="Invalid request")

    return Response(status_code=201)


@app.get(
    "/clicks",
    response_model=list[Click],
    summary="Retrieve clicks",
    description="Endpoint to retrieve clicks before or after a certain date.",
)
def retrieve_clicks(
    date_value: date = Query(
        ...,
        alias="date",
        description="The date to filter clicks.",
        examples=["2022-01-01"],
    ),
    direction: Literal["<", ">", "<=", ">="] = Query(
        ...,
        description="Specify '<', '<=' or '>' etc. to filter clicks before, before or at or after the date.",
    ),
):
    start_of_day = datetime.combine(date_value, time.min, tzinfo=timezone.utc).isoformat()
    end_of_day = datetime.combine(date_value, time.max, tzinfo=timezone.utc).isoformat()

    if direction in ("<", "<="):
        compare_value = start_of_day if direction == "<" else end_of_day
    else:
        compare_value = end_of_day if direction == ">" else start_of_day

    query = f"SELECT id, timestamp FROM clicks WHERE timestamp {direction} ? ORDER BY timestamp ASC"

    try:
        with get_db() as conn:
            rows = conn.execute(query, (compare_value,)).fetchall()
    except sqlite3.Error:
        raise HTTPException(status_code=400, detail="Invalid request")

    if not rows:
        raise HTTPException(status_code=404, detail="No clicks found")

    result = []
    for row in rows:
        result.append(
            Click(
                id=row["id"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
            )
        )

    return result


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)