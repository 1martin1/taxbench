import os
import sqlite3
import uuid
from contextlib import closing
from datetime import date, datetime, time, timezone
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict


DB_PATH = "db.sqlite3"


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(get_db_connection()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clicks (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL
            )
            """
        )
        conn.commit()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class Click(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    timestamp: datetime


app = FastAPI(
    title="Click Tracking API",
    version="1.0.0",
    description="API for registering and retrieving user clicks.",
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.post("/click", status_code=201, responses={400: {"description": "Invalid request"}})
def register_click() -> Response:
    click_id = str(uuid.uuid4())
    timestamp = utc_now_iso()

    try:
        with closing(get_db_connection()) as conn:
            conn.execute(
                "INSERT INTO clicks (id, timestamp) VALUES (?, ?)",
                (click_id, timestamp),
            )
            conn.commit()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=400, detail=f"Database error: {exc}") from exc

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
    date_value: date = Query(..., alias="date", description="The date to filter clicks."),
    direction: Literal["<", ">", "<=", ">="] = Query(
        ...,
        description="Specify '<', '<=' or '>' etc. to filter clicks before, before or at or after the date.",
    ),
) -> list[Click]:
    if direction in ("<", "<="):
        boundary = datetime.combine(date_value, time.min, tzinfo=timezone.utc)
    else:
        boundary = datetime.combine(date_value, time.min, tzinfo=timezone.utc)

    boundary_iso = boundary.isoformat()

    try:
        with closing(get_db_connection()) as conn:
            rows = conn.execute(
                f"SELECT id, timestamp FROM clicks WHERE timestamp {direction} ? ORDER BY timestamp ASC",
                (boundary_iso,),
            ).fetchall()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=400, detail=f"Database error: {exc}") from exc

    if not rows:
        raise HTTPException(status_code=404, detail="No clicks found")

    return [
        Click(id=row["id"], timestamp=parse_iso_datetime(row["timestamp"]))
        for row in rows
    ]


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)